#!/usr/bin/python

# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for cros_portage_upgrade.py."""

import cStringIO
import exceptions
import optparse
import os
import re
import sys
import tempfile
import unittest

import mox

import chromite.lib.table as table
import merge_package_status as mps

class MergeTest(unittest.TestCase):
  """Test the functionality of merge_package_status."""

  # These taken from cros_portage_upgrade column names.
  COL_VER_x86 = 'Current x86 Version'
  COL_VER_arm = 'Current arm Version'

  COL_CROS_TARGET = 'ChromeOS Root Target'
  COL_HOST_TARGET = 'Host Root Target'
  COL_CMP_ARCH = 'Comparing arm vs x86 Versions'

  COLUMNS = [mps.COL_PACKAGE,
             mps.COL_SLOT,
             mps.COL_OVERLAY,
             COL_VER_x86,
             COL_VER_arm,
             mps.COL_TARGET,
             ]

  ROW0 = {mps.COL_PACKAGE: 'lib/foo',
          mps.COL_SLOT: '0',
          mps.COL_OVERLAY: 'portage',
          COL_VER_x86: '1.2.3',
          COL_VER_arm: '1.2.3',
          mps.COL_TARGET: 'chromeos chromeos-dev hard-host-depends'}
  ROW0_PROCESSED_TARGETS = 'chromeos-dev hard-host-depends'
  ROW0_OUT = dict(ROW0)
  ROW0_OUT[mps.COL_TARGET] = ROW0_PROCESSED_TARGETS
  ROW0_FINAL = dict(ROW0)
  ROW0_FINAL[mps.COL_PACKAGE] = ROW0[mps.COL_PACKAGE] + ':' + ROW0[mps.COL_SLOT]
  ROW0_FINAL[COL_CROS_TARGET] = 'chromeos chromeos-dev'
  ROW0_FINAL[COL_HOST_TARGET] = 'hard-host-depends'
  ROW0_FINAL[COL_CMP_ARCH] = 'same'

  ROW1 = {mps.COL_PACKAGE: 'dev/bar',
          mps.COL_SLOT: '0',
          mps.COL_OVERLAY: 'chromiumos-overlay',
          COL_VER_x86: '1.2.3',
          COL_VER_arm: '1.2.3-r1',
          mps.COL_TARGET: 'chromeos'}
  ROW1_PROCESSED_TARGETS = 'chromeos'
  ROW1_OUT = dict(ROW1)
  ROW1_OUT[mps.COL_TARGET] = ROW1_PROCESSED_TARGETS
  ROW1_FINAL = dict(ROW1)
  ROW1_FINAL[COL_CROS_TARGET] = 'chromeos'
  ROW1_FINAL[COL_HOST_TARGET] = ''
  ROW1_FINAL[COL_CMP_ARCH] = 'different'

  ROW2 = {mps.COL_PACKAGE: 'lib/foo',
          mps.COL_SLOT: '1',
          mps.COL_OVERLAY: 'portage',
          COL_VER_x86: '1.2.3',
          COL_VER_arm: '',
          mps.COL_TARGET: 'chromeos chromeos-dev world'}
  ROW2_PROCESSED_TARGETS = 'chromeos-dev world'
  ROW2_OUT = dict(ROW2)
  ROW2_OUT[mps.COL_TARGET] = ROW2_PROCESSED_TARGETS
  ROW2_FINAL = dict(ROW2)
  ROW2_FINAL[mps.COL_PACKAGE] = ROW2[mps.COL_PACKAGE] + ':' + ROW2[mps.COL_SLOT]
  ROW2_FINAL[COL_CROS_TARGET] = 'chromeos chromeos-dev'
  ROW2_FINAL[COL_HOST_TARGET] = 'world'
  ROW2_FINAL[COL_CMP_ARCH] = ''

  def setUp(self):
    self._table = self._CreateTableWithRows(self.COLUMNS,
                                            [self.ROW0, self.ROW1, self.ROW2])

  def _CreateTableWithRows(self, cols, rows):
    mytable = table.Table(list(cols))
    if rows:
      for row in rows:
        mytable.AppendRow(dict(row))
    return mytable

  def _CreateTmpCsvFile(self, table):
    fd, path = tempfile.mkstemp(text=True)
    tmpfile = open(path, 'w')
    table.WriteCSV(tmpfile)
    tmpfile.close()
    return path

  def _GetFullRowFor(self, row, cols):
    return dict((col, row.get(col, '')) for col in cols)

  def assertRowsEqual(self, row1, row2):
    # Determine column superset
    cols = set(row1.keys() + row2.keys())
    self.assertEquals(self._GetFullRowFor(row1, cols),
                      self._GetFullRowFor(row2, cols))

  def testGetCrosTargetRank(self):
    cros_rank = mps._GetCrosTargetRank('chromeos')
    crosdev_rank = mps._GetCrosTargetRank('chromeos-dev')
    crostest_rank = mps._GetCrosTargetRank('chromeos-test')
    other_rank = mps._GetCrosTargetRank('foobar')

    self.assertTrue(cros_rank)
    self.assertTrue(crosdev_rank)
    self.assertTrue(crostest_rank)
    self.assertFalse(other_rank)
    self.assertTrue(cros_rank < crosdev_rank)
    self.assertTrue(crosdev_rank < crostest_rank)

  def testProcessTargets(self):
    test_in = [
        ['chromeos', 'chromeos-dev'],
        ['world', 'chromeos', 'chromeos-dev', 'chromeos-test'],
        ['world', 'hard-host-depends', 'chromeos-dev', 'chromeos-test'],
        ]
    test_out = [
        ['chromeos-dev'],
        ['chromeos-test', 'world'],
        ['chromeos-test', 'hard-host-depends', 'world'],
        ]
    test_rev_out = [
        ['chromeos'],
        ['chromeos', 'world'],
        ['chromeos-dev', 'hard-host-depends', 'world'],
        ]

    for input, good_out, rev_out in zip(test_in, test_out, test_rev_out):
      output = mps._ProcessTargets(input)
      self.assertEquals(output, good_out)
      output = mps._ProcessTargets(input, reverse_cros=True)
      self.assertEquals(output, rev_out)

  def testProcessRowTargetValue(self):
    for in_row in (self.ROW0, self.ROW1, self.ROW2):
      tmp_row = dict(in_row)
      mps._ProcessRowTargetValue(tmp_row)
      for col in in_row:
        if col == mps.COL_TARGET:
          proc_targ = ' '.join(mps._ProcessTargets(in_row[col].split()))
          self.assertEquals(proc_targ, tmp_row[col])
        else:
          self.assertEquals(in_row[col], tmp_row[col])

  def testLoadTable(self):
    path = self._CreateTmpCsvFile(self._table)
    csv_table = mps.LoadTable(path)
    for ix, row_out in enumerate((self.ROW0_OUT, self.ROW1_OUT, self.ROW2_OUT)):
      self.assertRowsEqual(row_out, csv_table[ix])

    os.unlink(path)

  def testLoadTables(self):
    # Create a second table to merge with standard table.
    row0_2 = {mps.COL_PACKAGE: 'lib/foo',
              mps.COL_SLOT: '1',
              mps.COL_OVERLAY: 'portage',
              self.COL_VER_arm: '1.2.4',
              mps.COL_TARGET: 'chromeos chromeos-dev world'}
    row1_2 = {mps.COL_PACKAGE: 'dev/bar',
              mps.COL_SLOT: '0',
              mps.COL_OVERLAY: 'chromiumos-overlay',
              self.COL_VER_arm: '1.2.3-r1',
              mps.COL_TARGET: 'chromeos chromeos-dev chromeos-test'}
    row2_2 = {mps.COL_PACKAGE: 'dev/newby',
              mps.COL_SLOT: '2',
              mps.COL_OVERLAY: 'chromiumos-overlay',
              self.COL_VER_arm: '3.2.1',
              mps.COL_TARGET: 'chromeos hard-host-depends'}
    cols = [col for col in self.COLUMNS if col != self.COL_VER_x86]
    table_2 = self._CreateTableWithRows(cols,
                                        [row0_2, row1_2, row2_2])

    # Minor patch to main table for this test.
    self._table.GetRowByIndex(2)[self.COL_VER_arm] = '1.2.4'

    path1 = self._CreateTmpCsvFile(self._table)
    path2 = self._CreateTmpCsvFile(table_2)

    combined_table = mps.LoadTables([path1, path2])

    final_row0 = {mps.COL_PACKAGE: 'dev/bar',
                  mps.COL_SLOT: '0',
                  mps.COL_OVERLAY: 'chromiumos-overlay',
                  self.COL_VER_x86: '1.2.3',
                  self.COL_VER_arm: '1.2.3-r1',
                  mps.COL_TARGET: 'chromeos'}
    final_row1 = {mps.COL_PACKAGE: 'dev/newby',
                  mps.COL_SLOT: '2',
                  mps.COL_OVERLAY: 'chromiumos-overlay',
                  self.COL_VER_x86: '',
                  self.COL_VER_arm: '3.2.1',
                  mps.COL_TARGET: 'chromeos hard-host-depends'}
    final_row2 = {mps.COL_PACKAGE: 'lib/foo',
                  mps.COL_SLOT: '0',
                  mps.COL_OVERLAY: 'portage',
                  self.COL_VER_x86: '1.2.3',
                  self.COL_VER_arm: '1.2.3',
                  mps.COL_TARGET: 'chromeos-dev hard-host-depends'}
    final_row3 = {mps.COL_PACKAGE: 'lib/foo',
                  mps.COL_SLOT: '1',
                  mps.COL_OVERLAY: 'portage',
                  self.COL_VER_x86: '1.2.3',
                  self.COL_VER_arm: '1.2.4',
                  mps.COL_TARGET: 'chromeos-dev world'}

    final_rows = (final_row0, final_row1, final_row2, final_row3)
    for ix, row_out in enumerate(final_rows):
      self.assertRowsEqual(row_out, combined_table[ix])

    os.unlink(path1)
    os.unlink(path2)

  def testFinalizeTable(self):
    self.assertEquals(3, self._table.GetNumRows())
    self.assertEquals(len(self.COLUMNS), self._table.GetNumColumns())

    mps.FinalizeTable(self._table)

    self.assertEquals(3, self._table.GetNumRows())
    self.assertEquals(len(self.COLUMNS) + 3, self._table.GetNumColumns())

    final_rows = (self.ROW0_FINAL, self.ROW1_FINAL, self.ROW2_FINAL)
    for ix, row_out in enumerate(final_rows):
      self.assertRowsEqual(row_out, self._table[ix])

class MainTest(mox.MoxTestBase):
  """Test argument handling at the main method level."""

  def setUp(self):
    """Setup for all tests in this class."""
    mox.MoxTestBase.setUp(self)

  def _StartCapturingOutput(self):
    """Begin capturing stdout and stderr."""
    self._stdout = sys.stdout
    self._stderr = sys.stderr
    sys.stdout = self._stdout_cap = cStringIO.StringIO()
    sys.stderr = self._stderr_cap = cStringIO.StringIO()

  def _RetrieveCapturedOutput(self):
    """Return captured output so far as (stdout, stderr) tuple."""
    try:
      return (self._stdout_cap.getvalue(), self._stderr_cap.getvalue())
    except AttributeError:
      # This will happen if output capturing isn't on.
      return None

  def _StopCapturingOutput(self):
    """Stop capturing stdout and stderr."""
    try:
      sys.stdout = self._stdout
      sys.stderr = self._stderr
    except AttributeError:
      # This will happen if output capturing wasn't on.
      pass

  def _PrepareArgv(self, *args):
    """Prepare command line for calling merge_package_status.main"""
    sys.argv = [ re.sub("_unittest", "", sys.argv[0]) ]
    sys.argv.extend(args)

  def testHelp(self):
    """Test that --help is functioning"""
    self._PrepareArgv("--help")

    # Capture stdout/stderr so it can be verified later
    self._StartCapturingOutput()

    # Running with --help should exit with code==0
    try:
      mps.main()
    except exceptions.SystemExit, e:
      self.assertEquals(e.args[0], 0)

    # Verify that a message beginning with "Usage: " was printed
    (stdout, stderr) = self._RetrieveCapturedOutput()
    self._StopCapturingOutput()
    self.assertTrue(stdout.startswith("Usage: "))

  def testMissingOut(self):
    """Test that running without --out exits with an error."""
    self._PrepareArgv("")

    # Capture stdout/stderr so it can be verified later
    self._StartCapturingOutput()

    # Running without --out should exit with code!=0
    try:
      mps.main()
    except exceptions.SystemExit, e:
      self.assertNotEquals(e.args[0], 0)

    # Verify that a message containing "ERROR: " was printed
    (stdout, stderr) = self._RetrieveCapturedOutput()
    self._StopCapturingOutput()
    self.assertTrue("ERROR:" in stderr)

  def testMissingPackage(self):
    """Test that running without a package argument exits with an error."""
    self._PrepareArgv("--out=any-out")

    # Capture stdout/stderr so it can be verified later
    self._StartCapturingOutput()

    # Running without a package should exit with code!=0
    try:
      mps.main()
    except exceptions.SystemExit, e:
      self.assertNotEquals(e.args[0], 0)

    # Verify that a message containing "ERROR: " was printed
    (stdout, stderr) = self._RetrieveCapturedOutput()
    self._StopCapturingOutput()
    self.assertTrue("ERROR:" in stderr)

  def testMain(self):
    """Verify that running main method runs LoadTables, WriteTable."""
    self.mox.StubOutWithMock(mps, 'LoadTables')
    self.mox.StubOutWithMock(mps, 'WriteTable')
    mps.LoadTables(mox.IgnoreArg()).AndReturn('csv_table')
    mps.WriteTable(mox.Regex(r'csv_table'), 'any-out')
    self.mox.ReplayAll()

    self._PrepareArgv("--out=any-out", "any-package")
    mps.main()
    self.mox.VerifyAll()

  def testMainWithFinalize(self):
    """Verify that running main method runs LoadTables, WriteTable."""
    self.mox.StubOutWithMock(mps, 'LoadTables')
    self.mox.StubOutWithMock(mps, 'FinalizeTable')
    self.mox.StubOutWithMock(mps, 'WriteTable')
    mps.LoadTables(mox.IgnoreArg()).AndReturn('csv_table')
    mps.FinalizeTable(mox.Regex(r'csv_table'))
    mps.WriteTable(mox.Regex(r'csv_table'), 'any-out')
    self.mox.ReplayAll()

    self._PrepareArgv("--out=any-out", "--finalize-for-upload", "any-package")
    mps.main()
    self.mox.VerifyAll()

if __name__ == '__main__':
  unittest.main()
