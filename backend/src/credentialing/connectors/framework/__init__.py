"""Shared connector machinery, written once for every source.

HTTP fetching with change detection and failure mapping, snapshot building, delimited-file
reading, value and name parsing, row accounting and instrumentation. A connector subpackage
declares its capabilities and maps its rows; everything else lives here.
"""
