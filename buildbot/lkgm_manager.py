#!/usr/bin/python
# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
A library to generate and store the manifests for cros builders to use.
"""

import logging
import os
import re
import time

from chromite.buildbot import manifest_version
from chromite.lib import cros_build_lib as cros_lib


class PromoteCandidateException(Exception):
  """Exception thrown for failure to promote manifest candidate."""
  pass


def _SyncGitRepo(local_dir):
  """"Clone Given git repo
  Args:
    local_dir: location with repo that should be synced.
  """
  cros_lib.RunCommand(['git', 'remote', 'update'], cwd=local_dir)
  cros_lib.RunCommand(['git', 'rebase', 'origin/master'], cwd=local_dir)


class _LKGMCandidateInfo(manifest_version.VersionInfo):
  """Class to encapsualte the chrome os lkgm candidate info

  You can instantiate this class in two ways.
  1)using a version file, specifically chromeos_version.sh,
  which contains the version information.
  2) just passing in the 4 version components (major, minor, sp, patch and
    revision number),
  Args:
    version_string: Optional version string to parse rather than from a file
    ver_maj: major version
    ver_min: minor version
    ver_sp:  sp version
    ver_patch: patch version
    ver_revision: version revision
    version_file: version file location.
  """
  LKGM_RE = '(\d+\.\d+\.\d+\.\d+)(?:-rc(\d+))?'

  def __init__(self, version_string=None, version_file=None):
    self.ver_revision = None
    if version_string:
      match = re.search(self.LKGM_RE, version_string)
      assert match, 'LKGM did not re %s' % self.LKGM_RE
      super(_LKGMCandidateInfo, self).__init__(match.group(1),
                                               incr_type='branch')
      if match.group(2):
        self.ver_revision = int(match.group(2))

    else:
      super(_LKGMCandidateInfo, self).__init__(version_file=version_file,
                                               incr_type='branch')
    if not self.ver_revision:
      self.ver_revision = 1

  def VersionString(self):
    """returns the full version string of the lkgm candidate"""
    return '%s.%s.%s.%s-rc%s' % (self.ver_maj, self.ver_min, self.ver_sp,
                                 self.ver_patch, self.ver_revision)

  @classmethod
  def VersionCompare(cls, version_string):
    """Useful method to return a comparable version of a LKGM string."""
    lkgm = cls(version_string)
    return map(int, [lkgm.ver_maj, lkgm.ver_min, lkgm.ver_sp, lkgm.ver_patch,
                     lkgm.ver_revision])

  def IncrementVersion(self, message=None, dry_run=False):
    """Increments the version by incrementing the revision #."""
    self.ver_revision += 1
    return self.VersionString()


class LKGMManager(manifest_version.BuildSpecsManager):
  """A Class to manage lkgm candidates and their states.

  Vars:
    lkgm_subdir:  Subdirectory within manifest repo to store candidates.
  """
  # Max timeout before assuming other builders have failed.
  MAX_TIMEOUT_SECONDS = 300
  # Polling timeout for checking git repo for other build statuses.
  SLEEP_TIMEOUT = 30

  # Sub-directories for LKGM and Chrome LKGM's.
  LKGM_SUBDIR = 'LKGM-candidates'
  CHROME_PFQ_SUBDIR = 'chrome-LKGM-candidates'

  # Set path in repository to keep latest approved LKGM manifest.
  LKGM_PATH = 'LKGM/lkgm.xml'

  def __init__(self, source_dir, checkout_repo, manifest_repo, branch,
               build_name, build_type, clobber=False,
               dry_run=True):
    """Initialize an LKGM Manager.

    Args:
      build_type:  Type of build.  Must be either chrome or binary.
    Other args see manifest_version.BuildSpecsManager.
    """
    super(LKGMManager, self).__init__(
        source_dir=source_dir, checkout_repo=checkout_repo,
        manifest_repo=manifest_repo, branch=branch, build_name=build_name,
        incr_type='branch', clobber=clobber, dry_run=dry_run)

    self.compare_versions_fn = _LKGMCandidateInfo.VersionCompare

    assert build_type in ('chrome', 'binary')
    if build_type == 'chrome':
      self.lkgm_subdir = self.CHROME_PFQ_SUBDIR
    else:
      self.lkgm_subdir = self.LKGM_SUBDIR

  def _LoadSpecs(self, version_info):
    """Loads the specifications from the working directory.
    Args:
      version_info: Info class for version information of cros.
    """
    super(LKGMManager, self)._LoadSpecs(version_info, self.lkgm_subdir)

  def _GetLatestCandidateByVersion(self, version_info):
    """Returns the latest lkgm candidate corresponding to the version file.
    Args:
      version_info: Info class for version information of cros.
    """
    if self.all:
      matched_lkgms = filter(
          lambda ver: ver.startswith(version_info.VersionString()), self.all)
      if matched_lkgms:
        return _LKGMCandidateInfo(sorted(matched_lkgms,
                                         key=self.compare_versions_fn)[-1])

    return _LKGMCandidateInfo(version_info.VersionString())


  def _SetInFlightWithRetry(self, commit_message, retries):
    for index in range(retries+1):
      try:
        self._SetInFlight(commit_message)
        break
      except (manifest_version.GitCommandException,
              cros_lib.RunCommandError) as e:
        last_error = 'Failed to set build in-flight: %s' % e
        logging.error(last_error)
        logging.error('Retrying:  Retry %d/%d' %
                      (index + 1, retries + 1))
    else:
      raise manifest_version.GenerateBuildSpecException(last_error)

  def CreateNewCandidate(self, version_file, retries=3):
    """Gets the version number of the next build spec to build.
      Args:
        version_file: File to use in cros when checking for cros version.
        retries: Number of retries for updating the status
      Returns:
        next_build: a string of the next build number for the builder to consume
                    or None in case of no need to build.
      Raises:
        GenerateBuildSpecException in case of failure to generate a buildspec
    """
    try:
      version_info = self._GetCurrentVersionInfo(version_file)
      self._LoadSpecs(version_info)
      lkgm_info = self._GetLatestCandidateByVersion(version_info)

      self.current_version = self._CreateNewBuildSpec(lkgm_info)
      if self.current_version:
        logging.debug('Using build spec: %s', self.current_version)
        commit_message = 'Automatic: Start %s %s' % (self.build_name,
                                                     self.current_version)
        self._SetInFlightWithRetry(commit_message, retries)

      return self.GetLocalManifest(self.current_version)

    except (cros_lib.RunCommandError,
            manifest_version.GitCommandException) as e:
      err_msg = 'Failed to generate LKGM Candidate. error: %s' % e
      logging.error(err_msg)
      raise manifest_version.GenerateBuildSpecException(err_msg)

  def GetLatestCandidate(self, version_file, retries=5):
    """Gets the version number of the next build spec to build.
      Args:
        version_file: File to use in cros when checking for cros version.
        retries: Number of retries for updating the status
      Returns:
        Local path to manifest to build or None in case of no need to build.
      Raises:
        GenerateBuildSpecException in case of failure to generate a buildspec
    """
    try:
      version_info = self._GetCurrentVersionInfo(version_file)
      self._LoadSpecs(version_info)
      self.current_version = self.latest_unprocessed
      if self.current_version:
        logging.debug('Using build spec: %s', self.current_version)
        commit_message = 'Automatic: Start %s %s' % (self.build_name,
                                                     self.current_version)
        self._SetInFlightWithRetry(commit_message, retries)

      return self.GetLocalManifest(self.current_version)

    except (cros_lib.RunCommandError,
            manifest_version.GitCommandException) as e:
      err_msg = 'Failed to get next LKGM Candidate. error: %s' % e
      logging.error(err_msg)
      raise manifest_version.GenerateBuildSpecException(err_msg)

  def GetBuildersStatus(self, builders_array):
    """Returns a build-names->status dictionary of build statuses."""
    xml_name = self.current_version + '.xml'

    # Set some default location strings.
    dir_pfx = _LKGMCandidateInfo(self.current_version).DirPrefix()
    specs_for_build = os.path.join(
        self.manifests_dir, self.lkgm_subdir, 'build-name', '%(build_name)s')
    pass_file = os.path.join(specs_for_build, 'pass', dir_pfx, xml_name)
    fail_file = os.path.join(specs_for_build, 'fail', dir_pfx, xml_name)
    inflight_file = os.path.join(specs_for_build, 'inflight', dir_pfx, xml_name)

    start_time = time.time()
    builder_statuses = {}
    num_complete = 0

    # Monitor the repo until all builders report in or we've waited too long.
    while (time.time() - start_time) < self.MAX_TIMEOUT_SECONDS:
      _SyncGitRepo(self.manifests_dir)
      for builder in builders_array:
        if builder_statuses.get(builder, None) not in ['pass', 'fail']:
          logging.debug("Checking for builder %s's status" % builder)
          builder_pass = pass_file % {'build_name': builder}
          builder_fail = fail_file % {'build_name': builder}
          builder_inflight = inflight_file % {'build_name': builder}
          if os.path.lexists(builder_pass):
            builder_statuses[builder] = 'pass'
            num_complete += 1
            logging.info('Builder %s completed with status passed', builder)
          elif os.path.lexists(builder_fail):
            builder_statuses[builder] = 'fail'
            num_complete += 1
            logging.info('Builder %s completed with status failed', builder)
          elif os.path.lexists(builder_inflight):
            builder_statuses[builder] = 'inflight'
          else:
            builder_statuses[builder] = None
            logging.debug('No status found for builder %s.' % builder)

      if num_complete < len(builders_array):
        logging.info('Waiting for other builds to complete')
        time.sleep(self.SLEEP_TIMEOUT)
      else:
        break

    if num_complete != len(builders_array):
      logging.error('Not all builds finished before MAX_TIMEOUT reached.')

    return builder_statuses

  def PromoteCandidate(self, retries=5):
    """Promotes the current LKGM candidate to be a real versioned LKGM."""
    assert self.current_version, 'No current manifest exists.'

    path_to_candidate = self.GetLocalManifest(self.current_version)
    path_to_lkgm = os.path.join(self.manifests_dir, self.LKGM_PATH)
    assert os.path.exists(path_to_candidate), 'Candidate not found locally.'

    # This may potentially fail for not being at TOT while pushing.
    for index in range(0, retries + 1):
      try:
        self._PrepSpecChanges()
        manifest_version.CreateSymlink(path_to_candidate, path_to_lkgm)
        cros_lib.RunCommand(['git', 'add', self.LKGM_PATH],
                            cwd=self.manifests_dir)
        self._PushSpecChanges('Automatic: %s promoting %s to LKGM' % (
                                  self.build_name, self.current_version))
        return
      except (manifest_version.GitCommandException,
              cros_lib.RunCommandError) as e:
          last_error = 'Failed to promote manifest. error: %s' % e
          logging.error(last_error)
          logging.error('Retrying to promote manifest:  Retry %d/%d' %
                        (index + 1, retries))
    else:
      raise PromoteCandidateException(last_error)
