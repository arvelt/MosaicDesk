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
    assert batch.output_folder(folder)==folder/'mosaicdesk_output'
    existing=batch.output_folder(folder); existing.mkdir()
    (existing/'keep.txt').write_text('keep')
    assert batch.output_folder(folder)==existing
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
    monkeypatch.setattr(batch,'process',lambda src,out,params,**kw:original_process(src,out,params,lambda *a,**k:[],**kw))
    events=[]
    assert batch.run(args,lambda *event:events.append(event))==0
    assert [p.name for p in (folder/'mosaicdesk_output').iterdir()]==['picture.png']
    assert not list(tmp_path.glob('*.processing*.json'))
    assert events[0][:2]==(0,1) and events[-1][:3]==(1,1,None)
    report=events[-1][-1]
    assert report['images'][0]['outside_pixels_equal']

def test_optional_log_outside_output(tmp_path,monkeypatch):
    folder=tmp_path/'images'; folder.mkdir()
    Image.new('RGB',(40,60),'blue').save(folder/'picture.png')
    args=SimpleNamespace(input=str(folder),output=None,no_open=True,include_nipple=False,confidence=.25,padding=10,block=20,save_log=True)
    original_process=batch.process
    monkeypatch.setattr(batch,'process',lambda src,out,params,**kw:original_process(src,out,params,lambda *a,**k:[],**kw))
    assert batch.run(args)==0
    assert [p.name for p in (folder/'mosaicdesk_output').iterdir()]==['picture.png']
    report=json.loads((folder/'mosaicdesk_output.processing.json').read_text(encoding='utf-8'))
    assert report['settings']['save_log'] and report['images'][0]['outside_pixels_equal']

if __name__=='__main__': print(make_samples())


def test_input_paths_deduplicate_and_ignore_nested_output(tmp_path):
    import pytest
    folder=tmp_path/'images'; folder.mkdir()
    for name in ('001.png','002.PNG','003.png'): Image.new('RGB',(40,60),'blue').save(folder/name)
    (folder/'ignored.jpg').write_bytes(b'not a PNG')
    existing=folder/'mosaicdesk_output'; existing.mkdir()
    Image.new('RGB',(40,60),'red').save(existing/'001.png')
    sources=batch.collect_inputs([folder,folder/'001.png',folder/'002.PNG'])
    assert len(sources)==3 and all(p.parent==folder for p in sources)
    assert batch.resolve_output([folder/'001.png'])==existing
    assert batch.resolve_output(sources)==existing
    with pytest.raises(ValueError): batch.collect_inputs([folder/'ignored.jpg'])


def test_mixed_folders_require_common_output_and_names_are_not_renumbered(tmp_path):
    import pytest
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    for p in (a/'same.png',b/'same.png'): Image.new('RGB',(10,10),'blue').save(p)
    images=batch.collect_inputs([a/'same.png',b/'same.png'])
    with pytest.raises(ValueError,match='共通'): batch.resolve_output(images)
    output=tmp_path/'chosen'
    assert batch.resolve_output(images,output)==output
    with pytest.raises(ValueError,match='同じファイル名'): batch.output_paths(images,output)
    with pytest.raises(ValueError,match='元画像'): batch.output_paths([a/'same.png'],a)
    assert not (a/'mosaicdesk_output').exists() and not (b/'mosaicdesk_output').exists()


import pytest
@pytest.mark.parametrize('with_regions', [False,True])
@pytest.mark.parametrize('strip', [False,True])
def test_all_generation_chunks_and_resave(tmp_path,with_regions,strip):
    src=tmp_path/'source.png'; output=tmp_path/'mosaicdesk_output'/'source.png'
    rng=np.random.default_rng(88)
    image=Image.fromarray(rng.integers(0,256,(65,73,4),dtype=np.uint8),'RGBA')
    png=PngImagePlugin.PngInfo()
    png.add_text('parameters','prompt, negative prompt, seed, steps, CFG, model, sampler')
    png.add_text('workflow','ComfyUI workflow',zip=True)
    png.add_itxt('prompt','ComfyUI prompt')
    exif=Image.Exif(); exif[274]=6
    image.save(src,pnginfo=png,exif=exif,dpi=(96,96),icc_profile=b'synthetic ICC fixture')
    forbidden={b'tEXt',b'zTXt',b'iTXt',b'eXIf'}
    assert forbidden<=set(batch.png_chunk_types(src))
    original_hash=hashlib.sha256(src.read_bytes()).hexdigest()
    args=SimpleNamespace(block=8,padding=0,confidence=.25,include_nipple=False,strip_metadata=strip)
    regions=[dict(id='a',box=[5,5,25,25],block=8)] if with_regions else []
    for _ in range(2):
        entry,_=batch.process(src,output,args,regions=regions)
        chunks=set(batch.png_chunk_types(output))
        assert not chunks&forbidden if strip else b'eXIf' in chunks
        with Image.open(output) as saved:
            assert saved.info['icc_profile']==b'synthetic ICC fixture'
            assert round(saved.info['dpi'][0])==96
            assert ('parameters' not in saved.info) if strip else (saved.info['parameters'].startswith('prompt'))
            if not strip:
                assert saved.info['workflow']=='ComfyUI workflow' and saved.info['prompt']=='ComfyUI prompt'
        assert entry['outside_pixels_equal'] and entry['metadata_removed']==strip
    assert hashlib.sha256(src.read_bytes()).hexdigest()==original_hash
    assert list(output.parent.iterdir())==[output]


def test_rerender_only_one_file_and_one_region_from_original(tmp_path):
    from copy import deepcopy
    folder=tmp_path/'images'; folder.mkdir(); output=folder/'mosaicdesk_output'
    rng=np.random.default_rng(3)
    sources=[]
    for name in ('001.png','002.png','003.png'):
        src=folder/name; Image.fromarray(rng.integers(0,256,(140,170,3),dtype=np.uint8)).save(src); sources.append(src)
    args=SimpleNamespace(block=8,padding=0,confidence=.25,include_nipple=False,strip_metadata=True)
    regions=[dict(id='a',box=[5,5,35,35],block=8),dict(id='b',box=[50,20,80,50],block=8),dict(id='c',box=[100,80,135,120],block=8)]
    for src in sources: batch.process(src,output/src.name,args,regions=regions)
    untouched={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in (output/'001.png',output/'003.png')}
    originals={p:p.read_bytes() for p in sources}
    with Image.open(output/'002.png') as saved: before=np.asarray(saved).copy()
    edited=deepcopy(regions); edited[1]['box']=[50,55,80,85]; edited[1]['block']=12
    # A corrupt/different existing result must never be used as the render source.
    Image.new('RGB',(170,140),'black').save(output/'002.png')
    batch.process(sources[1],output/'002.png',args,regions=edited)
    with Image.open(output/'002.png') as saved: after=np.asarray(saved)
    with Image.open(sources[1]) as source: original=np.asarray(source)
    assert np.array_equal(after[20:50,50:80],original[20:50,50:80])
    assert np.array_equal(after[5:35,5:35],before[5:35,5:35])
    assert np.array_equal(after[80:120,100:135],before[80:120,100:135])
    assert not np.array_equal(after[55:85,50:80],original[55:85,50:80])
    for p,(content,mtime) in untouched.items(): assert p.read_bytes()==content and p.stat().st_mtime_ns==mtime
    for p,content in originals.items(): assert p.read_bytes()==content
    assert sorted(p.name for p in output.iterdir())==['001.png','002.png','003.png']


def test_failed_render_keeps_existing_output(tmp_path,monkeypatch):
    src=tmp_path/'source.png'; out=tmp_path/'completed'/'source.png'
    Image.new('RGB',(40,60),'blue').save(src)
    args=SimpleNamespace(block=8,padding=0,confidence=.25,include_nipple=False,strip_metadata=True)
    batch.process(src,out,args,regions=[])
    expected=out.read_bytes()
    monkeypatch.setattr(batch.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('replace refused')))
    with pytest.raises(OSError,match='replace refused'): batch.process(src,out,args,regions=[])
    assert out.read_bytes()==expected and list(out.parent.iterdir())==[out]
