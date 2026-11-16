"""Visual calibration from an actual owner-phone launch frame, not tap coordinates."""
from PIL import Image

# Observed TECNO 2436x1080 Standoff launch artwork on 2026-11-16.
# A match only authorizes another bounded wait/capture, never a tap or ingest.
LOADING_SIGNATURES=tuple(int(value,16) for value in (
    '13201ba012b0a29224922092633322b7a2a5a22590a5d0274133152711071007',
    '13291aa11ab1a2712472269263b322b7a2b5b2a5d2a5d4a7c93315271107310f'))


def loading_signature(image):
    w,h=image.size
    sample=image.convert('L').crop((round(w*.05),round(h*.1),round(w*.95),round(h*.85))).resize((17,16))
    pixels=sample.load();value=0
    for y in range(16):
        for x in range(16):value=(value<<1)|int(pixels[x,y]>pixels[x+1,y])
    return value


def known_loading_artwork(image):
    signature=loading_signature(image)
    return min((signature^reference).bit_count() for reference in LOADING_SIGNATURES)<=16
