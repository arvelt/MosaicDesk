"""Verification fixtures are nonsexual; detections are injected for pixel tests."""
from pathlib import Path
from types import SimpleNamespace
import hashlib, json
import numpy as np
from PIL import Image, PngImagePlugin
import batch

def make_samples():
    folder=batch.ROOT/'sample-input'
    folder.mkdir(exist_ok=True)
    rng=np.random.default_rng(42)
    for i in range(12):
        mode=['RGB','RGBA','L','LA'][i%4]
        shape=(384+i*5,512+i*7)
        channels={'RGB':3,'RGBA':4,'L':0,'LA':2}[mode]
        arr=rng.integers(0,256,shape+((channels,) if channels else ()),dtype=np.uint8)
        img=Image.fromarray(arr,mode)
        meta=PngImagePlugin.PngInfo(); meta.add_text('parameters','nonsexual synthetic test fixture')
        # Nontrivial EXIF orientation verifies that raw dimensions stay fixed.
        exif=Image.Exif(); exif[274]=6
        img.save(folder/f'sample-{i+1:02d}.png',pnginfo=meta,exif=exif)
    return folder

def test_mask_preservation(tmp_path):
    args=SimpleNamespace(include_nipple=False,confidence=.25,padding=10,block=20)
    rng=np.random.default_rng(12)
    for mode in ['RGB','RGBA','L','LA','P']:
        arr=rng.integers(0,256,(97,131,4 if mode=='RGBA' else 3 if mode=='RGB' else 2 if mode=='LA' else 1),dtype=np.uint8)
        img=Image.fromarray(arr.squeeze() if mode in ('L','P') else arr, 'L' if mode=='P' else mode)
        if mode=='P': img=img.convert('P'); img.info['transparency']=0
        src=tmp_path/f'{mode}.png'; dest=tmp_path/f'{mode}-out.png'
        img.save(src)
        digest=hashlib.sha256(src.read_bytes()).hexdigest()
        def fake_detector(*a,**k): return [((0,3,21,33),'penis',.9),((80,40,200,120),'pussy',.8),((40,40,50,50),'nipple_f',.95)]
        entry,result=batch.process(src,dest,args,fake_detector)
        assert len(entry['detections'])==2 and entry['outside_pixels_equal']
        original=img.convert('RGBA') if mode=='P' else img
        mask=np.zeros((97,131),dtype=bool)
        for d in entry['detections']:
            x0,y0,x1,y1=d['box']; mask[y0:y1,x0:x1]=True
        with Image.open(dest) as saved: after=np.asarray(saved)
        before=np.asarray(original)
        assert np.array_equal(before[~mask],after[~mask])
        assert np.any(before[mask]!=after[mask])
        assert digest==hashlib.sha256(src.read_bytes()).hexdigest()

def test_undetected_exact_copy(tmp_path):
    src=tmp_path/'source.png'; dst=tmp_path/'out.png'
    Image.new('RGB',(29,37),'blue').save(src)
    args=SimpleNamespace(include_nipple=False,confidence=.25,padding=10,block=20)
    entry,_=batch.process(src,dst,args,lambda *a,**k:[])
    assert src.read_bytes()==dst.read_bytes() and entry['outside_pixels_equal']

def test_color_key_transparency(tmp_path):
    args=SimpleNamespace(include_nipple=False,confidence=.25,padding=0,block=20)
    src=tmp_path/'transparent.png'; dst=tmp_path/'out.png'
    img=Image.new('RGB',(80,60),'blue'); img.putpixel((1,1),(255,0,0))
    img.save(src,transparency=(255,0,0))
    entry,_=batch.process(src,dst,args,lambda *a,**k:[((20,20,40,40),'penis',.9)])
    with Image.open(src) as original, Image.open(dst) as saved:
        assert original.convert('RGBA').getpixel((1,1))==saved.getpixel((1,1))
        assert saved.mode=='RGBA' and entry['outside_pixels_equal']

def test_reject_16bit(tmp_path):
    import pytest
    src=tmp_path/'16bit.png'; dst=tmp_path/'out.png'
    Image.fromarray(np.arange(100,dtype=np.uint16).reshape(10,10)).save(src)
    args=SimpleNamespace(include_nipple=False,confidence=.25,padding=0,block=20)
    with pytest.raises(ValueError,match='16-bit'):
        batch.process(src,dst,args,lambda *a,**k:[])
    assert not dst.exists()

def test_output_name_and_existing_preservation(tmp_path):
    folder=tmp_path/'日本語 フォルダ'; folder.mkdir()
    assert batch.output_folder(folder)==tmp_path/'日本語 フォルダ-censored'
    existing=batch.output_folder(folder); existing.mkdir()
    (existing/'keep.txt').write_text('keep')
    assert batch.output_folder(folder)==tmp_path/'日本語 フォルダ-censored-2'
    assert (existing/'keep.txt').read_text()=='keep'

def test_independent_region_expansion_and_clipping():
    import pytest
    assert batch.expand_region((30,40,80,100),10,200,150)==(20,30,90,110)
    assert batch.expand_region((0,0,200,150),20,200,150)==(0,0,200,150)
    assert batch.expand_region((190,140,230,190),10,200,150)==(180,130,200,150)
    with pytest.raises(ValueError): batch.expand_region((250,40,260,50),0,200,150)

def test_output_contains_only_png_and_progress_report_is_in_memory(tmp_path, monkeypatch):
    folder=tmp_path/'images'; folder.mkdir()
    Image.new('RGB',(40,60),'blue').save(folder/'picture.png')
    args=SimpleNamespace(input=str(folder),output=None,no_open=True,include_nipple=False,confidence=.25,padding=10,block=20)
    original_process=batch.process
    monkeypatch.setattr(batch,'process',lambda src,out,params:original_process(src,out,params,lambda *a,**k:[]))
    events=[]
    assert batch.run(args,lambda *event:events.append(event))==0
    assert [p.name for p in (tmp_path/'images-censored').iterdir()]==['picture.png']
    assert not list(tmp_path.glob('*.processing*.json'))
    assert events[0][:2]==(0,1) and events[-1][:3]==(1,1,None)
    report=events[-1][-1]
    assert report['images'][0]['outside_pixels_equal']

def test_optional_log_outside_output(tmp_path,monkeypatch):
    folder=tmp_path/'images'; folder.mkdir()
    Image.new('RGB',(40,60),'blue').save(folder/'picture.png')
    args=SimpleNamespace(input=str(folder),output=None,no_open=True,include_nipple=False,confidence=.25,padding=10,block=20,save_log=True)
    original_process=batch.process
    monkeypatch.setattr(batch,'process',lambda src,out,params:original_process(src,out,params,lambda *a,**k:[]))
    assert batch.run(args)==0
    assert [p.name for p in (tmp_path/'images-censored').iterdir()]==['picture.png']
    report=json.loads((tmp_path/'images-censored.processing.json').read_text(encoding='utf-8'))
    assert report['settings']['save_log'] and report['images'][0]['outside_pixels_equal']

if __name__=='__main__': print(make_samples())
