"""Import first in heavy pipeline scripts to enable NAS I/O fair-share.

Worker-hosted scripts (run via masonjar_worker) must complete all NAS/file I/O
on the runpy main stack before returning. The worker calls
io_fairshare.deactivate() after each job; do not leave background I/O that
continues after __main__ returns.
"""
import io_fairshare

io_fairshare.activate()
