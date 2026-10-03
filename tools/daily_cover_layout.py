import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, features

from tools.cover_quality import FONT, validate_cover_text

LAYOUT_VERSION = 'buyer-comparison-b-v1'
PROFILES = (
    {'key': 'pique_vs_fibre', 'facts': {'pique_tuck_structure'},
     'terms': (r'pique|पिके|पीके', r'fibre|fiber|फाइबर', r'knit|बुनावट|बुनाई|construction'),
     'labels': ('बुनावट', 'फाइबर'), 'latin': ('KNIT STRUCTURE', 'FIBRE CONTENT'), 'icons': ('loops', 'yarn')},
    {'key': 'weight_question', 'facts': {'fabric_mass_per_area'},
     'terms': (r'gsm|जी.?एस.?एम', r'light|हल्क|halk', r'heavy|heavier|भारी|bhar', r'quality|क्वालिटी|गुणवत्ता|बेहतर|better'),
     'labels': ('हल्का', 'भारी'), 'latin': ('LIGHTER', 'HEAVIER'), 'icons': ('fabric_light', 'fabric_heavy')},
    {'key': 'area_vs_garment', 'facts': {'fabric_mass_per_area'},
     'terms': (r'gsm|square|वर्ग|मीटर', r'garment|t.?shirt|टी.?शर्ट|पूरी|poori'),
     'labels': ('कपड़े का GSM', 'पूरी टी-शर्ट'), 'latin': ('FABRIC GSM', 'WHOLE TEE'), 'icons': ('area', 'tee')},
    {'key': 'surface_vs_shrinkage', 'facts': {'biopolish_surface', 'compaction_shrinkage'},
     'terms': (r'bio|बायो|smooth|नरम|soft|मुलायम', r'shrink|सिकुड़|सिकुड|compac|कम्पैक'),
     'labels': ('सतह की नरमी', 'धुलाई में सिकुड़न'), 'latin': ('SURFACE FEEL', 'WASH SHRINKAGE'), 'icons': ('smooth', 'tee')},
    {'key': 'terry_vs_fleece', 'facts': {'french_terry_fleece'},
     'terms': (r'terry|टेरी', r'fleece|फ्लीस', r'loop|लूप|फंद', r'brush|ब्रश|nap|रोए'),
     'labels': ('अंदर लूप', 'ब्रश की सतह'), 'latin': ('INSIDE LOOPS', 'BRUSHED INSIDE'), 'icons': ('loops', 'brush')},
    {'key': 'jersey_sides', 'facts': {'jersey_face_back'},
     'terms': (r'jersey|जर्सी', r'face|front|सामने|सीधी', r'back|reverse|पीछे|उल्टी'),
     'labels': ('सामने', 'पीछे'), 'latin': ('FRONT', 'BACK'), 'icons': ('stitch_v', 'loops')},
    {'key': 'yarn_numbering', 'facts': {'cotton_count_direction'},
     'terms': (r'\bne\b|कॉटन.?काउंट|cotton.?count', r'denier|डेनियर'),
     'labels': ('कॉटन काउंट', 'डेनियर'), 'latin': ('COTTON COUNT', 'DENIER'), 'icons': ('yarn', 'yarn')},
    {'key': 'knit_vs_fibre', 'facts': {'knit_loop_stretch'},
     'terms': (r'knit|बुनाई|फंद|loop', r'stretch|खिंच|खींच', r'elastane|इलास्टेन|लाइक्रा|lycra|fibre|fiber|फाइबर'),
     'labels': ('बुनाई', 'फाइबर'), 'latin': ('KNIT STRUCTURE', 'FIBRE CONTENT'), 'icons': ('loops', 'yarn')},
    # DTF launch lessons (3-Oct-2026). Order matters: a lesson citing several DTF facts takes the first profile whose
    # facts it cites and whose comparison its script really speaks.
    {'key': 'dtf_white_vs_transparent', 'facts': {'dtf_transparent_background'},
     'terms': (r'transparent|ट्रांसपेरेंट|पारदर्शी', r'background|बैकग्राउंड|white|सफ़ेद|सफेद|safed|rectangle|रेक्टेंगल|box|बॉक्स|डिब्बा'),
     'labels': ('सफ़ेद बैकग्राउंड', 'ट्रांसपेरेंट'), 'latin': ('WHITE BACKGROUND', 'TRANSPARENT'), 'icons': ('print_box', 'print_clean')},
    {'key': 'dtf_canva_size', 'facts': {'dtf_canva_png_size'},
     'terms': (r'canva|कैनवा', r'3\.125|size|साइज़|साइज|साईज'),
     'labels': ('साइज़ 1x', 'साइज़ 3.125x'), 'latin': ('SIZE 1x', 'SIZE 3.125x'), 'icons': ('pixelated', 'sharp')},
    {'key': 'dtf_pieces_vs_designs', 'facts': {'dtf_pieces_whole_sheet'},
     'terms': (r'piece|पीस|pcs', r'sheet|शीट', r'design|डिज़ाइन|डिजाइन|डिज़ाईन'),
     'labels': ('डिज़ाइन', 'पूरी शीट'), 'latin': ('DESIGNS', 'WHOLE SHEETS'), 'icons': ('designs_loose', 'stack_sheets')},
    {'key': 'dtf_dpi_vs_pixels', 'facts': {'dtf_min_resolution'},
     'terms': (r'dpi|डीपीआई', r'pixel|पिक्सल|पिक्सेल|\bpx\b'),
     'labels': ('सिर्फ़ DPI नंबर', 'असली पिक्सल'), 'latin': ('DPI NUMBER ONLY', 'REAL PIXELS'), 'icons': ('pixelated', 'sharp')},
    {'key': 'dtf_separate_vs_gang', 'facts': {'dtf_sheet_size'},
     'terms': (r'gang|गैंग|ek sheet|एक शीट|one sheet|single sheet|kai design|कई डिज़ाइन|many designs|multiple designs', r'design|डिज़ाइन|डिजाइन'),
     'labels': ('अलग-अलग शीट', 'एक गैंग शीट'), 'latin': ('SEPARATE SHEETS', 'ONE GANG SHEET'), 'icons': ('many_sheets', 'gang_sheet')},
    {'key': 'dtf_tee_vs_hoodie', 'facts': {'dtf_press_settings'},
     'terms': (r'165', r'180', r'press|प्रेस'),
     'labels': ('टी-शर्ट 165°C', 'हुडी 180°C'), 'latin': ('TEE 165°C', 'HOODIE 180°C'), 'icons': ('tee', 'hoodie')},
)


def supported_comparison(topic, script):
    brief = getattr(topic, 'brief', None)
    if not isinstance(brief, dict):
        return None
    ids = set(brief.get('fact_ids') or ())
    evidence = brief.get('evidence') or {}
    if not isinstance(evidence, dict) or not ids.issubset(evidence):
        return None
    for profile in PROFILES:
        if profile['facts'].issubset(ids) and all(re.search(pattern, script or '', re.I) for pattern in profile['terms']):
            return profile
    return None


def _font(size):
    font = ImageFont.truetype(str(FONT), size)
    try:
        font.set_variation_by_axes([800])
    except OSError:
        pass
    return font


def _text(draw, text, area, maximum, minimum, color, boxes):
    left, top, right, bottom = area
    for size in range(maximum, minimum - 1, -2):
        font = _font(size)
        box = draw.textbbox((0, 0), text, font=font)
        width, height = box[2] - box[0], box[3] - box[1]
        if width <= right-left and height <= bottom-top:
            x = left + (right-left-width)/2 - box[0]
            y = top + (bottom-top-height)/2 - box[1]
            draw.text((x,y), text, font=font, fill=color)
            boxes.append({'text':text,'bounds':[round(x+box[0]),round(y+box[1]),round(x+box[2]),round(y+box[3])],'font_size':size})
            return
    raise ValueError('Rewrite the complete cover phrase; it does not fit readably')


def _star(cx, cy, outer, inner, points=5):
    import math
    return [(cx+(outer if i%2==0 else inner)*math.cos(math.radians(-90+i*180/points)),
             cy+(outer if i%2==0 else inner)*math.sin(math.radians(-90+i*180/points))) for i in range(points*2)]


def _inside(polygon, x, y):
    hit=False
    for (x1,y1),(x2,y2) in zip(polygon,polygon[1:]+polygon[:1]):
        if (y1>y)!=(y2>y) and x<(x2-x1)*(y-y1)/(y2-y1)+x1:
            hit=not hit
    return hit


def _badge(draw, cx, cy, r, colours=('#ff5a36','#00a6a6')):
    """The sample artwork on every DTF icon: an orange disc carrying a teal star."""
    draw.ellipse((cx-r,cy-r,cx+r,cy+r),fill=colours[0])
    draw.polygon(_star(cx,cy,r*.66,r*.28),fill=colours[1])


def _sheet(draw, box, ink, line, fill='#fff9e7'):
    draw.rounded_rectangle(box,radius=max(2,round((box[2]-box[0])*.06)),fill=fill,outline=ink,width=line)


def _design_grid(draw, box, cols, rows, fill=.34):
    left,top,right,bottom=box
    cw,ch=(right-left)/cols,(bottom-top)/rows
    r=min(cw,ch)*fill
    palette=(('#ff5a36','#00a6a6'),('#7b2cbf','#ffd60a'),('#06d6a0','#073b4c'))
    for row in range(rows):
        for col in range(cols):
            _badge(draw,left+cw*(col+.5),top+ch*(row+.5),r,palette[(row+col)%len(palette)])


def _tee_shape(left, top, width, height):
    points=[(.30,.13),(.41,.07),(.59,.07),(.70,.13),(.96,.30),(.80,.53),(.70,.45),(.70,.93),(.30,.93),(.30,.45),(.20,.53),(.04,.30)]
    return [(left+x*width,top+y*height) for x,y in points]


def _icon(draw, name, area, ink):
    left,top,right,bottom=area
    width,height=right-left,bottom-top
    scale=width/300
    line=max(2,round(6*scale))
    if name in ('print_box','print_clean'):
        draw.polygon(_tee_shape(left,top,width,height),fill='#183f51',outline=ink,width=line)
        draw.arc((left+.4*width,top-.02*height,left+.6*width,top+.20*height),0,180,fill='#fff9e7',width=line)
        cx,cy,r=left+.5*width,top+.48*height,min(width,height)*.15
        if name=='print_box':
            pad=r*1.45
            draw.rectangle((cx-pad,cy-pad,cx+pad,cy+pad),fill='#ffffff')
        _badge(draw,cx,cy,r)
    elif name in ('pixelated','sharp'):
        side=min(width,height)*.86
        x0,y0=left+(width-side)/2,top+(height-side)/2
        cx,cy,r=x0+side/2,y0+side/2,side/2
        star=_star(cx,cy,r*.66,r*.28)
        if name=='sharp':
            _badge(draw,cx,cy,r)
        else:
            cells=13
            cell=side/cells
            for row in range(cells):
                for col in range(cells):
                    x,y=x0+cell*(col+.5),y0+cell*(row+.5)
                    colour='#00a6a6' if _inside(star,x,y) else '#ff5a36' if (x-cx)**2+(y-cy)**2<=r*r else None
                    if colour:
                        draw.rectangle((x0+cell*col,y0+cell*row,x0+cell*(col+1),y0+cell*(row+1)),fill=colour)
    elif name=='many_sheets':
        gap=width*.06
        w,h=(width-gap*3)/2,(height-gap*3)/2
        for row in range(2):
            for col in range(2):
                box=(left+gap+col*(w+gap),top+gap+row*(h+gap),left+gap+col*(w+gap)+w,top+gap+row*(h+gap)+h)
                _sheet(draw,box,ink,max(2,line//2))
                _badge(draw,(box[0]+box[2])/2,(box[1]+box[3])/2,min(w,h)*.28)
    elif name=='gang_sheet':
        w=min(width*.62,height*.62)
        box=(left+(width-w)/2,top+height*.04,left+(width+w)/2,bottom-height*.04)
        _sheet(draw,box,ink,line)
        inset=w*.08
        _design_grid(draw,(box[0]+inset,box[1]+inset,box[2]-inset,box[3]-inset),3,4)
    elif name=='stack_sheets':
        w=min(width*.56,height*.56)
        for k in (2,1,0):
            off=k*width*.08
            box=(left+(width-w)/2-width*.06+off,top+height*.10-off*.7,left+(width+w)/2-width*.06+off,bottom-height*.04-off*.7)
            _sheet(draw,box,ink,max(2,line//2))
            if k==0:
                inset=w*.08
                _design_grid(draw,(box[0]+inset,box[1]+inset,box[2]-inset,box[3]-inset),3,4)
    elif name=='designs_loose':
        _design_grid(draw,(left+width*.04,top+height*.06,right-width*.04,bottom-height*.06),3,3,fill=.40)
    elif name=='hoodie':
        draw.ellipse((left+.33*width,top+.00*height,left+.67*width,top+.34*height),fill='#fff9e7',outline=ink,width=line)
        body=[(.33,.20),(.67,.20),(.90,.31),(.98,.86),(.85,.89),(.77,.46),(.76,.96),(.24,.96),(.23,.46),(.15,.89),(.02,.86),(.10,.31)]
        draw.polygon([(left+x*width,top+y*height) for x,y in body],fill='#fff9e7',outline=ink,width=line)
        draw.ellipse((left+.41*width,top+.08*height,left+.59*width,top+.28*height),fill='#183f51')
        for x in (.45,.55):
            draw.line((left+x*width,top+.27*height,left+x*width,top+.44*height),fill=ink,width=max(2,line//2))
        draw.rounded_rectangle((left+.35*width,top+.66*height,left+.65*width,top+.84*height),radius=max(2,round(10*scale)),outline=ink,width=max(2,line//2))
    elif name=='tee':
        points=[(.30,.13),(.41,.07),(.59,.07),(.70,.13),(.96,.30),(.80,.53),(.70,.45),(.70,.93),(.30,.93),(.30,.45),(.20,.53),(.04,.30)]
        points=[(left+x*width,top+y*height) for x,y in points]
        draw.polygon(points,fill='#fff9e7',outline=ink,width=line)
        draw.arc((left+.4*width,top-.02*height,left+.6*width,top+.20*height),0,180,fill=ink,width=line)
    elif name in ('fabric_light','fabric_heavy','area','smooth'):
        pad=width*.05
        rect=(left+pad,top+height*.12,right-pad,bottom-height*.12)
        draw.rounded_rectangle(rect,radius=max(2,round(8*scale)),fill='#fff9e7',outline=ink,width=line)
        gap=round((30 if name=='fabric_light' else 14 if name=='fabric_heavy' else 23)*scale)
        if name!='smooth':
            for x in range(int(rect[0]+gap),int(rect[2]),max(3,gap)):
                draw.line((x,rect[1]+line,x,rect[3]-line),fill='#91a9aa',width=max(1,line//3))
        if name=='area':
            draw.line((rect[0],bottom-height*.03,rect[2],bottom-height*.03),fill=ink,width=line)
    elif name in ('loops','brush','stitch_v'):
        gap=max(8,round(43*scale))
        for y in range(int(top+height*.12),int(bottom-height*.08),gap):
            for x in range(int(left+width*.05),int(right-width*.1),gap):
                if name=='loops':
                    draw.arc((x,y,x+gap*.85,y+gap*.95),0,345,fill=ink,width=max(2,line//2))
                elif name=='stitch_v':
                    draw.line((x,y,x+gap*.4,y+gap*.8,x+gap*.8,y),fill=ink,width=max(2,line//2))
                else:
                    draw.line((x,y+gap*.8,x+gap*.45,y),fill=ink,width=max(2,line//2))
                    draw.line((x+gap*.3,y+gap*.8,x+gap*.8,y+gap*.2),fill=ink,width=max(2,line//2))
    else:
        for i in range(4):
            y=top+height*(.18+i*.19)
            draw.line((left+width*.07,y,right-width*.07,y),fill=ink,width=line)


def render_buyer_cover(scene, lines, output_path, *, topic='', script=''):
    lines=validate_cover_text(' | '.join(lines),script)
    needs_hindi=any(re.search(r'[\u0900-\u097f]',line) for line in lines)
    if needs_hindi and not features.check('raqm'):
        raise ValueError('Hindi cover requires RAQM shaping')
    profile=supported_comparison(topic,script)
    labels=profile['labels' if features.check('raqm') else 'latin'] if profile else ()
    output=Path(output_path)
    output.parent.mkdir(parents=True,exist_ok=True)
    metadata={'layout':LAYOUT_VERSION,'comparison_key':profile['key'] if profile else None,'labels':list(labels),'fact_ids':sorted(profile['facts']) if profile else [],'comparison_illustrated':bool(profile),'outputs':{}}
    for landscape in (False,True):
        width,height=(1280,720) if landscape else (1080,1920)
        img=Image.new('RGB',(width,height),'#f1ecdf')
        draw=ImageDraw.Draw(img);boxes=[]
        if landscape:
            safe=(368,0,912,720)
            areas=[(safe[0],80,safe[2],182),(safe[0],182,safe[2],276)]
            cards=[(366,328,626,618),(654,328,914,618)]
            label_top=550;label_bottom=609;heading_max,heading_min=90,34;label_max,label_min=39,26
            icon_pad,icon_top,icon_bottom=24,353,527
            scene_area=(367,325,913,646)
        else:
            areas=[(82,380,998,559),(82,560,998,758)]
            cards=[(100,945,510,1468),(570,945,980,1468)]
            label_top=1310;label_bottom=1445;heading_max,heading_min=151,72;label_max,label_min=62,40
            icon_pad,icon_top,icon_bottom=37,1000,1282
            scene_area=(90,935,990,1490)
        _text(draw,lines[0],areas[0],heading_max,heading_min,'#183446',boxes)
        _text(draw,lines[1],areas[1],heading_max,heading_min,'#143e55',boxes)
        if profile:
            for i,(card,label,name) in enumerate(zip(cards,labels,profile['icons'])):
                draw.rounded_rectangle(card,radius=15 if landscape else 24,fill='#8fc4d3' if i==0 else '#edc46c')
                _icon(draw,name,(card[0]+icon_pad,icon_top,card[2]-icon_pad,icon_bottom),'#183f51')
                _text(draw,label,(card[0]+12,label_top,card[2]-12,label_bottom),label_max,label_min,'#173b4c',boxes)
            _text(draw,'ILLUSTRATION',(368,660,912,698) if landscape else (90,1625,990,1685),22 if landscape else 29,18,'#617b82',boxes)
        else:
            if scene is None:
                raise ValueError('A non-comparison lesson needs its relevant source scene')
            left,top,right,bottom=scene_area
            image=ImageOps.fit(scene.convert('RGB'),(right-left,bottom-top),method=Image.Resampling.LANCZOS)
            img.paste(image,(left,top))
        target=output.with_name(output.stem+'_youtube.png') if landscape else output
        img.save(target,'PNG',optimize=True)
        if target.stat().st_size>=2_000_000:
            raise ValueError('Cover exceeds the YouTube API image limit')
        metadata['outputs']['youtube' if landscape else 'portrait']={'file':str(target),'size':[width,height],'text':boxes,'bytes':target.stat().st_size}
    return metadata
