# vim:set sw=4 ts=8 fileencoding=utf-8:
# SPDX-License-Identifier: MIT
# Copyright © 2023, Serguei E. Leontiev (leo@sai.msu.ru)
#
"""
`magic_doc.ipynb` as test of `cppimport.magic` and vice versa
=============================================================

1. Checking the successful calculation of the selected subset of cells
(tags: `fast`, `slow` and cells without these tags);

2. Comparison of all cell's outputs that don't have the `random` tag.
"""

import copy
import sys
import warnings

import nbformat
import pytest
from jupyter_client.manager import start_new_kernel
from nbconvert.preprocessors import ExecutePreprocessor

COVERAGE = True  # import & start coverage

DTE_RANDOMS = {"random", "random_long"}
DTE_SKIPS = {"skip", "skip_darwin", "skip_linux", "skip_win32"}
DTE_XFAILS = {"xfail", "xfail_darwin", "xfail_linux", "xfail_win32"}

# All kinds of notebook cell tags
DTA_TAGS = DTE_RANDOMS | DTE_SKIPS | DTE_XFAILS


def _get_stags(meta):
    stags = set(meta.get("tags", []))
    return stags


def _check_sxf(sxf, stags):
    for t in stags:
        if t == sxf or (
            t.startswith(sxf + "_") and sys.platform.startswith(t[len(sxf) + 1 :])
        ):
            return True
    return False


def _outputs_no_ec(c):
    return [
        {k: v for k, v in e.items() if k != "execution_count"}
        for e in c.get("outputs", [])
    ]


class SkipExecutePreprocessor(ExecutePreprocessor):
    """Selecting cells and clearing rejected."""

    def __init__(self, **kwargs):
        super(SkipExecutePreprocessor, self).__init__(**kwargs)

    def preprocess_cell(self, cell, resources, index):
        stags = _get_stags(cell.metadata)
        if _check_sxf("skip", stags):
            rcell, rresources = cell.copy(), resources
        else:
            allow_errors = self.allow_errors
            try:
                self.allow_errors = _check_sxf("xfail", stags)
                rcell, rresources = super(
                    SkipExecutePreprocessor, self
                ).preprocess_cell(cell, resources, index)
            finally:
                self.allow_errors = allow_errors
        return rcell, rresources


def test_magic_doc():
    """Calculation & comparison selected subset of cells."""

    with open("magic_doc.ipynb", "r") as f:
        tmd = nbformat.read(f, nbformat.NO_CONVERT)
        assert len(tmd.cells) > 1

    if COVERAGE:
        tmd.cells.insert(
            0,
            nbformat.v4.new_code_cell(
                "import coverage as _tdi_coverage\n"
                "_tdi_cov = _tdi_coverage.Coverage()\n"
                "_tdi_cov.start()\n"
            ),
        )
        tmd.cells.append(
            nbformat.v4.new_code_cell("_tdi_cov.stop()\n" "_tdi_cov.save()\n")
        )

    for t in tmd.cells:
        if t.cell_type == "code":
            if "execution" in t.metadata:  # TODO: remove
                del t.metadata["execution"]

    ep = SkipExecutePreprocessor(timeout=600)
    km, _ = start_new_kernel()  # Ignore kernelspec in magic_doc.ipynb
    emd, _ = ep.preprocess(copy.deepcopy(tmd), km=km)

    xfail_cells, xpass_cells = 0, 0

    assert len(tmd.cells) == len(emd.cells)
    for t, e in zip(tmd.cells, emd.cells):
        stags = _get_stags(t.metadata)
        if stags - DTA_TAGS:
            warnings.warn(
                Warning("Test magic_doc.ipynb unknown tags: " + str(stags - DTA_TAGS))
            )
        if any(o.output_type == "error" for o in e.get("outputs", [])):
            if _check_sxf("xfail", stags):
                xfail_cells += 1
                continue
            assert False, "Found 'error' 'outputs' in for cell: " + str(e)

        if DTE_RANDOMS & stags:
            continue

        if not _check_sxf("xfail", stags):
            assert _outputs_no_ec(t) == _outputs_no_ec(e), "for cell: " + str(t)
        elif _outputs_no_ec(t) == _outputs_no_ec(e):
            xpass_cells += 1
        else:
            xfail_cells += 1

    if xfail_cells or xpass_cells:
        msg = "\nXFAIL_CELLS = %d XPASS_CELLS = %d\n" % (xfail_cells, xpass_cells)
        warnings.warn(Warning(msg))
        if xfail_cells:
            pytest.xfail(msg)

    km.shutdown_kernel(now=True, restart=False)
