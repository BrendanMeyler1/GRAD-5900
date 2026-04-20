"""
browser/proactor_loop.py — Custom uvicorn loop factory for Windows + Playwright.

Problem
-------
Uvicorn 0.40 passes ``use_subprocess=True`` to the *built-in* loop factories
when spawning the reload worker subprocess, causing the built-in asyncio
factory to return ``SelectorEventLoop`` on Windows.  Playwright needs
``ProactorEventLoop`` to call ``asyncio.create_subprocess_exec()``
(browser launch).

How uvicorn handles *custom* loop factories (anything not in LOOP_FACTORIES)
-----------------------------------------------------------------------------
For a custom ``--loop`` string uvicorn does::

    return import_from_string(self.loop)   # returns our *function* directly

…and then passes that function as ``loop_factory`` to ``asyncio.run()``.
``asyncio.run()`` calls ``loop_factory()`` (zero args) and expects a **loop
instance** in return — NOT a class.

This is different from the built-in path where ``asyncio_loop_factory(...)``
returns a *class* that ``asyncio.run()`` then calls as a constructor.  If we
returned a class our factory would be called once to get the class, then the
class is what ``asyncio.run`` holds as ``_loop_factory``, it calls it with no
args and gets a *class* back instead of an instance, producing::

    TypeError: BaseEventLoop.create_task() missing 1 required positional argument

Solution
--------
Return a **ProactorEventLoop instance** directly on Windows.  This function
is used as the ``loop_factory`` callable for ``asyncio.run()``; returning an
instance satisfies the expected contract.

Usage (CLI)::

    uvicorn api.main:app --reload --port 8000 --loop browser.proactor_loop:factory

Usage (programmatic, run.py)::

    uvicorn.run("api.main:app", loop="browser.proactor_loop:factory", ...)
"""

from __future__ import annotations

import asyncio
import sys


def factory() -> asyncio.AbstractEventLoop:
    """
    Create and return the appropriate event loop for this platform.

    On Windows: ``ProactorEventLoop`` — required for
    ``asyncio.create_subprocess_exec()`` which Playwright calls to launch
    the browser binary.

    On Linux/macOS: ``SelectorEventLoop`` (same as uvicorn's default).

    Returns:
        A new event-loop **instance** ready for use by ``asyncio.run()``.
    """
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.SelectorEventLoop()
