"""Bounded, screenshot-driven launch polling for the existing protocol-1 APK."""
import asyncio
import json
import re

from . import collector
from .screenshot_ocr import _run_worker
from .collector_navigation import next_steps as navigate

MAX_STARTUP_FRAMES=5


async def _probe(raw):
    status,payload=await asyncio.wait_for(_run_worker(raw,module='app.collector_vision_worker'),timeout=14)
    if status:raise ValueError('Screen probe failed')
    value=json.loads(payload)
    if not isinstance(value,dict):raise ValueError('Invalid screen result')
    return value


def inspect_startup(raw):
    # FastAPI runs this synchronous endpoint in a worker thread. Image decoding
    # and Tesseract stay in a credential-free, resource-limited child process.
    try:return asyncio.run(_probe(raw))
    except Exception:return {'screen_type':'unknown','reason':'SCREEN_PROBE_FAILED'}


def advance(lease,label,analysis):
    match=re.fullmatch(r'startup_([0-4])',label)
    if not match:return navigate(lease,label,analysis)
    index=int(match.group(1));screen=analysis.get('screen_type')
    if screen not in {'blank','loading'}:
        return navigate(lease,label,analysis)
    if index+1>=MAX_STARTUP_FRAMES:
        analysis['reason']='BLACK_SCREEN_TIMEOUT' if screen=='blank' else 'GAME_LOAD_TIMEOUT'
        return []
    extra=[{'action':'wait','ms':10000},{'action':'wait','ms':10000},
           {'action':'capture','label':f'startup_{index+1}'}]
    # Keep the actual issued plan, including dynamic steps, under all APK
    # limits. Insert before the existing terminal done, as the APK does.
    analysis['reason']='WAITING_FOR_GAME'
    return collector.issue_steps(lease,extra)
