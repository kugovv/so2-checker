"""Pilot routes calibrated on real frames. Unknown screens authorize no input."""
import re


def _word(words,text,x1,y1,x2,y2,width,height):
    hits=[]
    for word in words:
        if not isinstance(word,dict) or str(word.get('text','')).casefold()!=text:continue
        fields=[word.get(k) for k in ('left','top','width','height')]
        if any(type(v) is not int or v<0 for v in fields):continue
        left,top,w,h=fields
        if not w or not h or left+w>width or top+h>height:continue
        x=(left+w/2)/width;y=(top+h/2)/height
        if x1<=x<=x2 and y1<=y<=y2:hits.append((x,y))
    return hits[0] if len(hits)==1 else None


def main_menu_target(analysis):
    # Only this owner's actually observed landscape viewport is calibrated.
    # Different resolutions/orientations need their own physical verification.
    width=analysis.get('width');height=analysis.get('height')
    if (width,height)!=(2436,1080) or analysis.get('screen_type')!='unknown':return None
    if not analysis.get('menu_play_button_visible'):return None
    words=analysis.get('words',[])
    if not isinstance(words,list):return None
    if not _word(words,'мои',.12,.18,.24,.27,width,height):return None
    if not _word(words,'друзья',.15,.18,.27,.27,width,height):return None
    if not _word(words,'играть',.80,.85,.98,.98,width,height):return None
    return _word(words,'найти',.12,.73,.27,.84,width,height)


def next_steps(lease,label,analysis):
    from .collector import issue_steps
    if re.fullmatch(r'startup_[0-4]',label):
        point=main_menu_target(analysis)
        if point:
            analysis['screen_type']='main_menu'
            analysis['reason']='OPENING_PLAYER_SEARCH'
            return issue_steps(lease,[{'action':'tap','x':point[0],'y':point[1]},
                                      {'action':'wait','ms':1500},{'action':'capture','label':'nav_search'}])
    analysis.setdefault('reason','CALIBRATION_REQUIRED')
    return []
