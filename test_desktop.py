import time
from types import SimpleNamespace
from pathlib import Path
from PIL import Image
from tkinterdnd2 import TkinterDnD
from desktop import App, validate_settings
import batch
import pytest

def test_mosaic_only_never_overwrites_unselected_original(tmp_path,monkeypatch,shared_root):
    root=shared_root; app=prepare_app(root,tmp_path,monkeypatch)
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    for folder in (a,b): Image.new('RGB',(40,40),'blue').save(folder/'same.png')
    try:
        app.add_paths([a/'same.png',b/'same.png'])
        app.metadata_check.invoke(); app.deselect_button.invoke(); app.selection_checks[0].invoke()
        with pytest.raises(ValueError,match='元画像'):
            app.check_destination(b)
        app.check_destination(tmp_path/'output')
        app.set_output(tmp_path/'output')
        assert app.selected_sources=={batch.path_key(a/'same.png')}
        app.metadata_check.invoke()
        with pytest.raises(ValueError,match='同じファイル名'):
            app.check_destination(tmp_path/'output')
    finally:
        clear_root(root)

def test_twelve_files_two_mosaics_global_metadata_and_reedit(tmp_path,monkeypatch,shared_root):
    import numpy as np
    from PIL import PngImagePlugin
    from copy import deepcopy
    root=shared_root
    app=prepare_app(root,tmp_path,monkeypatch,lambda *a,**kw:[((5,5,35,35),'pussy',.9)])
    folder=tmp_path/'input'; folder.mkdir()
    originals={}
    for i in range(12):
        path=folder/f'{i:02}.png'
        meta=PngImagePlugin.PngInfo(); meta.add_text('parameters','private'); meta.add_itxt('workflow','private')
        Image.fromarray(np.random.default_rng(i).integers(0,256,(40,40,3),dtype=np.uint8)).save(path,pnginfo=meta)
        originals[path]=path.read_bytes()
    try:
        app.load_paths([folder]); wait_processing(root,app)
        initial=deepcopy([d['detections'] for d in app.documents])
        app.deselect_button.invoke(); app.selection_checks[0].invoke(); app.selection_checks[1].invoke()
        app.start_button.invoke(); wait_processing(root,app)
        assert len(list(app.output.glob('*.png')))==12 and len(app.selected_sources)==2
        for i,d in enumerate(app.documents):
            with Image.open(d['source']) as original,Image.open(d['output_path']) as saved:
                assert (original.tobytes()!=saved.tobytes())==(i<2)
            assert not set(batch.png_chunk_types(d['output_path']))&{b'tEXt',b'zTXt',b'iTXt',b'eXIf'}
            assert d['detections']==initial[i] and len(d['applied_regions'])==int(i<2)
        untouched={Path(d['output_path']):(Path(d['output_path']).read_bytes(),Path(d['output_path']).stat().st_mtime_ns) for d in app.documents[1:]}
        app.open_viewer(0); root.update(); viewer=app.image_viewer
        viewer.edit_button.invoke(); root.update()
        viewer.selected_id=viewer.regions[0]['id']; viewer.refresh_fields(); viewer.fields['x'].set('10'); viewer.apply_fields()
        prior=Path(app.documents[0]['output_path']).read_bytes()
        viewer.save_button.invoke()
        assert not app.busy and Path(app.documents[0]['output_path']).read_bytes()==prior
        viewer.close(); app.start_button.invoke(); wait_processing(root,app)
        for path,(data,mtime) in untouched.items(): assert path.read_bytes()==data and path.stat().st_mtime_ns==mtime
        app.mosaic_check.invoke(); app.start_button.invoke(); wait_processing(root,app)
        for d in app.documents:
            assert d['detections'] and not d['applied_regions']
            assert app.edit_store.restore(d['source'])==d['detections']
        for path,data in originals.items(): assert path.read_bytes()==data
    finally:
        clear_root(root)

def test_replace_append_settings_navigation_and_metadata_without_selection(tmp_path,monkeypatch,shared_root):
    from pathlib import Path
    root=shared_root; calls=[]
    def detector(*a,**kw):
        calls.append(kw['conf_threshold']); return [((5,5,25,25),'pussy',.9)]
    app=prepare_app(root,tmp_path,monkeypatch,detector)
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    for directory in (a,b):
        for i in range(2): Image.new('RGB',(40,40),'blue').save(directory/f'{i}.png')
    try:
        app.load_paths([a]); wait_processing(root,app)
        assert app.output==a/'mosaicdesk_output'
        app.save_settings({**app.settings,'block':16,'padding':2,'confidence':.7}); wait_processing(root,app)
        assert calls[-2:]==[.7,.7]
        assert all(d['detections'][0]['block']==16 and d['detections'][0]['box']==[3,3,27,27] for d in app.documents)
        app.open_viewer(0); root.update(); viewer=app.image_viewer; viewer.zoom.set('200%')
        viewer.next_button.invoke(); root.update(); assert app.image_viewer.index==1 and app.image_viewer.zoom.get()=='200%'
        app.image_viewer.previous_button.invoke(); root.update(); assert app.image_viewer.index==0
        app.image_viewer.close()
        app.apply_document(0,[dict(id='manual',box=[1,1,20,20],block=4)])
        app.save_settings({**app.settings,'block':24}); wait_processing(root,app)
        assert app.documents[0]['detections'][0]['block']==4 and app.documents[1]['detections'][0]['block']==24
        app.set_output(tmp_path/'custom'); app.mosaic_check.invoke()
        app.load_paths([b/'0.png']); wait_processing(root,app)
        assert len(app.documents)==1 and app.output==b/'mosaicdesk_output' and not app.output_explicit
        assert not app.mosaic_enabled.get() and app.strip_metadata.get() and float(app.progress['value'])==0
        app.load_paths([b/'1.png'],append=True); wait_processing(root,app); assert len(app.documents)==2
        app.deselect_button.invoke(); assert str(app.start_button['state'])=='normal'
        app.start_button.invoke(); wait_processing(root,app)
        assert sorted(p.name for p in app.output.iterdir())==['0.png','1.png']
    finally:
        clear_root(root)

def test_detection_keeps_ui_event_loop_responsive(tmp_path,monkeypatch,shared_root):
    import threading
    entered=threading.Event(); release=threading.Event()
    def detector(*a,**kw):
        entered.set(); assert release.wait(5); return []
    root=shared_root; app=prepare_app(root,tmp_path,monkeypatch,detector)
    path=tmp_path/'one.png'; Image.new('RGB',(40,40),'blue').save(path)
    try:
        app.load_paths([path]); assert entered.wait(2) and app.busy
        heartbeat=[]; root.after(0,lambda:heartbeat.append(True)); root.update()
        assert heartbeat and not app.output
        release.set(); wait_processing(root,app)
        assert app.documents and not app.output.exists()
    finally:
        release.set(); clear_root(root)

@pytest.mark.parametrize('folder_input',[True,False])
def test_load_detects_boxes_execute_shows_output(tmp_path,monkeypatch,shared_root,folder_input):
    import numpy as np
    calls=[]
    def detector(*args,**kwargs):
        calls.append(True)
        return [((20,20,100,100),'pussy',.9)]
    app=prepare_app(shared_root,tmp_path,monkeypatch,detector)
    source=tmp_path/'input'/'one.png'; source.parent.mkdir()
    Image.fromarray(np.random.default_rng(41).integers(0,256,(120,160,3),dtype=np.uint8)).save(source)
    original_bytes=source.read_bytes()
    root=shared_root
    try:
        app.add_paths([source.parent if folder_input else source])
        assert len(calls)==1 and len(app.documents[0]['detections'])==1
        assert not app.output.exists()
        assert tuple(root.tk.call(str(app.photos[0]),'get',20,20))==(255,64,80)
        app.open_viewer(0); root.update(); viewer=app.image_viewer; viewer.render()
        assert viewer.images['modified'].tobytes()==viewer.images['original'].tobytes()
        assert len([item for item in viewer.canvas.find_all() if viewer.canvas.type(item)=='rectangle'])==1
        viewer.close()
        app.start_button.invoke(); wait_processing(root,app)
        assert len(calls)==1 and source.read_bytes()==original_bytes
        with Image.open(app.output/source.name) as saved,Image.open(source) as original:
            expected=saved.convert('RGB')
            assert saved.tobytes()!=original.tobytes()
            assert tuple(root.tk.call(str(app.photos[0]),'get',20,20))==expected.getpixel((20,20))
        app.open_viewer(0); root.update(); viewer=app.image_viewer; viewer.render()
        assert not viewer.pending_preview and not viewer.editing_enabled
        assert not [item for item in viewer.canvas.find_all() if viewer.canvas.type(item)=='rectangle']
        with Image.open(app.output/source.name) as saved:
            assert viewer.images['modified'].tobytes()==saved.convert('RGBA').tobytes()
        viewer.close()
    finally:
        clear_root(root)

def test_selection_only_runs_from_execute(tmp_path,monkeypatch,shared_root):
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'STATE_ROOT',tmp_path/'state')
    monkeypatch.setattr(batch,'detect_censors',lambda *a,**kw:[])
    sources=[tmp_path/f'{i}.png' for i in range(3)]
    for source in sources: Image.new('RGB',(40,40),'blue').save(source)
    root=shared_root
    args=SimpleNamespace(input=None,output=None,no_open=True,include_nipple=False,confidence=.25,padding=0,block=8)
    try:
        app=App(root,args); app.add_paths(sources)
        app.metadata_check.invoke()
        assert len(app.selected_sources)==3
        app.deselect_button.invoke()
        assert not app.selected_sources and str(app.start_button['state'])=='disabled'
        app.selection_checks[1].invoke()
        assert app.selected_sources=={batch.path_key(sources[1])}
        assert not app.output.exists() and not app.busy
        app.start_button.invoke(); wait_processing(root,app)
        assert sorted(p.name for p in app.output.iterdir())==['1.png']
        assert app.selected_sources=={batch.path_key(sources[1])}
        prior=(app.output/'1.png').read_bytes()
        app.select_all_button.invoke()
        assert len(app.selected_sources)==3
        app.deselect_button.invoke(); app.selection_checks[0].invoke()
        assert sorted(p.name for p in app.output.iterdir())==['1.png']
        app.start_button.invoke(); wait_processing(root,app)
        assert sorted(p.name for p in app.output.iterdir())==['0.png','1.png']
        assert (app.output/'1.png').read_bytes()==prior
    finally:
        clear_root(root)

def test_clear_progress_and_pending_original_preview(tmp_path,monkeypatch,shared_root):
    import numpy as np
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'STATE_ROOT',tmp_path/'state')
    source=tmp_path/'source.png'
    Image.fromarray(np.random.default_rng(10).integers(0,256,(120,160,3),dtype=np.uint8)).save(source)
    root=shared_root
    args=SimpleNamespace(input=None,output=None,no_open=True,include_nipple=False,confidence=.25,padding=0,block=8)
    try:
        app=App(root,args); root.geometry('1060x780+4000+4000'); root.deiconify()
        app.add_paths([source])
        regions=[dict(id='saved',box=[20,20,100,100],block=12)]
        app.save_document(0,regions); wait_processing(root,app)
        root.update()
        with Image.open(app.output/source.name) as saved:
            assert tuple(root.tk.call(str(app.photos[0]),'get',20,20))==saved.convert('RGB').getpixel((20,20))
        assert app.select_all_button.master is app.selection_bar
        assert app.deselect_button.master is app.selection_bar
        assert app.selection_bar.winfo_rooty()+app.selection_bar.winfo_height()<=app.canvas.winfo_rooty()
        app.open_viewer(0); root.update(); saved_view=app.image_viewer; saved_view.render()
        assert not saved_view.editing_enabled
        assert len([item for item in saved_view.canvas.find_all() if saved_view.canvas.type(item)=='rectangle'])==0
        saved_view.close()
        target=app.output/source.name; prior=(target.read_bytes(),target.stat().st_mtime_ns)
        app.progress.configure(maximum=1,value=1)
        app.clear_button.invoke()
        assert float(app.progress['value'])==0 and float(app.progress['maximum'])==100
        app.add_paths([source])
        assert tuple(root.tk.call(str(app.photos[0]),'get',20,20))==(255,64,80)
        assert app.documents[0]['detections']==regions
        app.open_viewer(0); root.update(); viewer=app.image_viewer; viewer.render()
        assert viewer.pending_preview and not viewer.editing_enabled
        assert viewer.images['modified'].tobytes()==viewer.images['original'].tobytes()
        assert len([item for item in viewer.canvas.find_all() if viewer.canvas.type(item)=='rectangle'])==1
        assert (target.read_bytes(),target.stat().st_mtime_ns)==prior
        viewer.close()
    finally:
        clear_root(root)

@pytest.mark.parametrize('mosaic,metadata',[(True,True),(True,False),(False,True),(False,False)])
def test_processing_toggles(tmp_path,monkeypatch,shared_root,mosaic,metadata):
    from PIL import PngImagePlugin
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'STATE_ROOT',tmp_path/'state')
    calls=[]
    def detector(*args,**kwargs):
        calls.append(True)
        return [((2,2,20,20),'pussy',.9)]
    monkeypatch.setattr(batch,'detect_censors',detector)
    source=tmp_path/'fixture.png'
    info=PngImagePlugin.PngInfo(); info.add_text('parameters','private prompt')
    Image.new('RGB',(40,40),'blue').save(source,pnginfo=info)
    root=shared_root
    args=SimpleNamespace(input=None,output=None,no_open=True,include_nipple=False,confidence=.25,padding=0,block=8)
    try:
        app=App(root,args); app.add_paths([source])
        calls.clear()
        if not mosaic: app.mosaic_check.invoke()
        if not metadata: app.metadata_check.invoke()
        assert app.start_button.cget('text')=='実行'
        if not mosaic and not metadata:
            assert str(app.start_button['state'])=='disabled'
            app.start()
            assert not app.busy and not calls and not app.output.exists()
        else:
            app.start_button.invoke(); wait_processing(root,app)
            entry=app.documents[0]
            assert entry['status']=='保存済み'
            assert not calls and bool(entry['applied_regions'])==mosaic and bool(entry['detections'])
            assert (b'tEXt' in batch.png_chunk_types(app.output/source.name) or b'iTXt' in batch.png_chunk_types(app.output/source.name))== (not metadata)
    finally:
        clear_root(root)

def test_metadata_only_and_opt_in_editor(tmp_path,monkeypatch,shared_root):
    from PIL import PngImagePlugin
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'STATE_ROOT',tmp_path/'state')
    def forbidden_detector(*args,**kwargs):
        raise AssertionError('Metadata-only must not invoke the detector')
    monkeypatch.setattr(batch,'detect_censors',lambda *a,**kw:[])
    source=tmp_path/'plain.png'
    meta=PngImagePlugin.PngInfo(); meta.add_text('parameters','private prompt')
    Image.new('RGBA',(320,240),(20,40,60,80)).save(source,pnginfo=meta)
    original=source.read_bytes()
    root=shared_root
    args=SimpleNamespace(input=None,output=None,no_open=True,include_nipple=False,confidence=.25,padding=10,block=20)
    try:
        app=App(root,args)
        root.geometry('1060x780+4000+4000'); root.deiconify()
        app.add_paths([source])
        monkeypatch.setattr(batch,'detect_censors',forbidden_detector)
        app.mosaic_enabled.set(False)
        app.strip_metadata.set(True)
        app.update_controls()
        app.start_button.invoke(); wait_processing(root,app)
        assert app.documents[0]['status']=='保存済み'
        target=tmp_path/'mosaicdesk_output'/'plain.png'
        assert not set(batch.png_chunk_types(target))&{b'tEXt',b'zTXt',b'iTXt',b'eXIf'}
        with Image.open(source) as a,Image.open(target) as b:
            assert a.size==b.size and a.tobytes()==b.tobytes()
        assert source.read_bytes()==original
        app.open_viewer(0); root.update()
        editor=app.image_viewer; editor.render()
        assert not editor.editing_enabled and not editor.panel.winfo_ismapped()
        editor.tool.set('add')
        x,y,scale=editor.placements['modified']
        editor.pointer_down(SimpleNamespace(x=x+20*scale,y=y+20*scale))
        assert editor.regions==[]
        editor.edit_button.invoke(); root.update(); editor.render()
        assert editor.editing_enabled and editor.panel.winfo_ismapped()
        editor.edit_button.invoke(); root.update(); editor.render()
        assert not editor.panel.winfo_ismapped()
        app.image_viewer.close()
    finally:
        clear_root(root)

@pytest.fixture(scope='module')
def shared_root():
    root=TkinterDnD.Tk(); root.withdraw()
    yield root
    root.destroy()

def clear_root(root):
    for timer in root.tk.call('after','info'): root.after_cancel(timer)
    for child in root.winfo_children(): child.destroy()
    root.withdraw()

def test_drop_process_and_back(tmp_path,monkeypatch,shared_root):
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'STATE_ROOT',tmp_path/'state')
    monkeypatch.setattr(batch,'detect_censors',lambda *a,**kw:[])
    folder=tmp_path/'日本語 フォルダ'; folder.mkdir()
    Image.new('RGB',(600,800),'blue').save(folder/'test.png')
    root=shared_root
    args=SimpleNamespace(input=None,output=None,no_open=True,include_nipple=False,confidence=.25,padding=10,block=20)
    try:
        app=App(root,args)
        root.geometry('1060x780+4000+4000')
        app.on_drop(SimpleNamespace(data='{'+str(folder)+'}')); wait_processing(root,app)
        assert app.folder==folder and '未処理' in app.status.get()
        assert not (folder/'mosaicdesk_output').exists()
        app.start()
        assert app.busy and '処理中' in app.status.get()
        deadline=time.monotonic()+30
        while app.busy and time.monotonic()<deadline:
            root.update(); time.sleep(.02)
        assert not app.busy and '処理・保存完了' in app.status.get()
        assert app.report['output']==str(folder/'mosaicdesk_output')
        assert len(app.photos)==1
        root.deiconify(); root.update()
        view=app.open_viewer(0); view.geometry('1200x850+4000+4000')
        deadline=time.monotonic()+3
        while not app.image_viewer.photos and time.monotonic()<deadline:
            root.update(); time.sleep(.02)
        assert app.viewer is view
        viewer=app.image_viewer
        assert view.winfo_ismapped() and viewer.canvas.winfo_width()>500
        assert len(viewer.photos)==1 and viewer.display_sizes[0][0]>270
        for mode,count in [('original',1),('both',2),('modified',1)]:
            viewer.mode.set(mode); viewer.render()
            assert len(viewer.photos)==count
        viewer.zoom.set('200%'); viewer.render()
        assert viewer.display_sizes==[(1200,1600)]
        viewer.mode.set('both'); viewer.zoom.set('100%'); viewer.render()
        assert viewer.display_sizes==[(600,800),(600,800)]
        assert len([x for x in viewer.canvas.find_all() if viewer.canvas.type(x)=='image'])==2
        # Invoke the actual Back button, leaving the list and completed report intact.
        button=view.winfo_children()[0].winfo_children()[0]
        assert '一覧に戻る' in button.cget('text')
        button.invoke()
        assert app.viewer is None and app.report is not None
        assert sorted(p.name for p in (folder/'mosaicdesk_output').iterdir())==['test.png']
    finally:
        clear_root(root)

def test_settings_persist_validate_and_reach_processing(tmp_path,monkeypatch,shared_root):
    import pytest
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'STATE_ROOT',tmp_path/'state')
    monkeypatch.setattr(batch,'detect_censors',lambda *a,**kw:[])
    root=shared_root
    args=SimpleNamespace(input=None,output=None,no_open=True,include_nipple=False,confidence=.25,padding=10,block=20)
    try:
        app=App(root,args)
        app.save_settings(dict(block='32',padding='16',confidence='0.18',include_nipple=True,save_log=True))
        assert app.settings['block']==32 and app.settings_path.exists()
        second=App(root,args)
        assert second.settings==app.settings
        for values in ({**app.settings,'block':1},{**app.settings,'padding':-1},{**app.settings,'confidence':float('nan')},{**app.settings,'confidence':1.1}):
            with pytest.raises(ValueError): validate_settings(values)
        folder=tmp_path/'input'; folder.mkdir(); Image.new('RGB',(40,60),'blue').save(folder/'one.png')
        app.select_folder(folder)
        captured=[]
        actual_run=batch.run
        def spy(params,progress):
            captured.append(vars(params).copy()); return actual_run(params,progress)
        monkeypatch.setattr(batch,'run',spy)
        app.start()
        deadline=time.monotonic()+15
        while app.busy and time.monotonic()<deadline:
            root.update();time.sleep(.02)
        assert not app.busy and captured[0]['block']==32 and captured[0]['confidence']==.18 and captured[0]['save_log']
        assert app.report['log_file'] and (folder/'mosaicdesk_output.processing.json').exists()
        settings=app.open_settings()
        assert settings.winfo_exists()
        settings.destroy()
    finally: clear_root(root)


def prepare_app(root,tmp_path,monkeypatch,detector=None):
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'STATE_ROOT',tmp_path/'state')
    monkeypatch.setattr(batch,'detect_censors',detector or (lambda *a,**k:[]))
    args=SimpleNamespace(input=None,output=None,no_open=True,include_nipple=False,confidence=.25,padding=0,block=8,save_log=False)
    app=App(root,args)
    root.geometry('1100x820+4000+4000'); root.deiconify(); root.update()
    return app


def wait_processing(root,app):
    deadline=time.monotonic()+15
    while app.busy and time.monotonic()<deadline: root.update(); time.sleep(.01)
    root.update()
    assert not app.busy and '失敗' not in app.status.get(),app.status.get()


def test_file_selection_and_multiple_drop_and_one_image_processing(tmp_path,monkeypatch,shared_root):
    from desktop import filedialog
    root=shared_root
    for name in ('001.png','002.png','003.png'): Image.new('RGB',(180,140),'blue').save(tmp_path/name)
    try:
        app=prepare_app(root,tmp_path,monkeypatch)
        monkeypatch.setattr(filedialog,'askopenfilenames',lambda **k:(str(tmp_path/'001.png'),))
        app.add_button.invoke(); wait_processing(root,app)
        assert len(app.documents)==1 and len(app.photos)==1
        app.start(); wait_processing(root,app)
        assert list((tmp_path/'mosaicdesk_output').iterdir())==[tmp_path/'mosaicdesk_output'/'001.png']
        first=(tmp_path/'mosaicdesk_output'/'001.png'); initial=(first.read_bytes(),first.stat().st_mtime_ns)
        monkeypatch.setattr(filedialog,'askopenfilenames',lambda **k:(str(tmp_path/'001.png'),str(tmp_path/'002.png')))
        app.choose_images(append=True); wait_processing(root,app); assert len(app.documents)==2
        app.on_drop(SimpleNamespace(data=root.tk.call('list',str(tmp_path/'002.png'),str(tmp_path/'003.png')))); wait_processing(root,app)
        assert len(app.documents)==2
        assert app.drop.dnd_bind('<<Drop>>') and app.add_button.dnd_bind('<<Drop>>')
        app.start(); wait_processing(root,app)
        assert first.read_bytes()==initial[0] and first.stat().st_mtime_ns==initial[1]
        assert sorted(p.name for p in (tmp_path/'mosaicdesk_output').iterdir())==['001.png','002.png','003.png']
        assert all(d['status']=='保存済み' for d in app.documents)
    finally: clear_root(root)


def test_mixed_input_and_output_selection(tmp_path,monkeypatch,shared_root):
    from desktop import filedialog,messagebox
    root=shared_root
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    Image.new('RGB',(180,140),'blue').save(a/'001.png'); Image.new('RGB',(180,140),'red').save(b/'002.png')
    try:
        app=prepare_app(root,tmp_path,monkeypatch)
        app.on_drop(SimpleNamespace(data=root.tk.call('list',str(a),str(b/'002.png')))); wait_processing(root,app)
        assert len(app.documents)==2 and app.output is None
        assert str(app.start_button['state'])=='disabled' and '共通' in app.status.get()
        monkeypatch.setattr(messagebox,'showinfo',lambda *a,**k:None)
        app.start(); assert not app.busy
        assert not (a/'mosaicdesk_output').exists() and not (b/'mosaicdesk_output').exists()
        chosen=tmp_path/'custom'
        monkeypatch.setattr(filedialog,'askdirectory',lambda **k:str(chosen))
        app.change_output_button.invoke(); assert app.output==chosen
        app.start(); wait_processing(root,app)
        assert sorted(p.name for p in chosen.iterdir())==['001.png','002.png']
        assert not (a/'mosaicdesk_output').exists() and not (b/'mosaicdesk_output').exists()
    finally: clear_root(root)


@pytest.mark.parametrize('folder_input',[True,False])
def test_region_editing_undo_and_selective_save(tmp_path,monkeypatch,shared_root,folder_input):
    import numpy as np
    from copy import deepcopy
    from PIL import PngImagePlugin
    root=shared_root
    folder=tmp_path/'images'; folder.mkdir()
    rng=np.random.default_rng(19)
    for name in ('001.png','002.png','003.png'):
        metadata=PngImagePlugin.PngInfo(); metadata.add_text('parameters','private synthetic prompt')
        Image.fromarray(rng.integers(0,256,(180,240,3),dtype=np.uint8)).save(folder/name,pnginfo=metadata)
    detector=lambda *a,**k:[((10,10,40,40),'penis',.9),((80,30,115,65),'pussy',.9),((160,100,200,150),'penis',.9)]
    try:
        app=prepare_app(root,tmp_path,monkeypatch,detector)
        if folder_input: app.select_folder(folder)
        else: app.add_paths([folder/'001.png',folder/'002.png',folder/'003.png'])
        assert app.strip_metadata.get() is True
        app.start(); wait_processing(root,app)
        output=folder/'mosaicdesk_output'
        untouched={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in (output/'001.png',output/'003.png')}
        with Image.open(output/'002.png') as im: baseline=np.asarray(im).copy()
        view=app.open_viewer(1); view.geometry('1200x850+4000+4000'); root.update()
        editor=app.image_viewer; editor.edit_button.invoke(); root.update(); editor.zoom.set('100%'); editor.render()
        initial=deepcopy(editor.regions)
        assert len(initial)==3
        editor.region_list.selection_set(1); editor.select_from_list()
        editor.fields['x'].set('85'); editor.fields['y'].set('70'); editor.fields['width'].set('40'); editor.fields['height'].set('25'); editor.fields['block'].set('12')
        editor.apply_fields(); editor.render()
        assert editor.regions[0]==initial[0] and editor.regions[2]==initial[2]
        changed=deepcopy(editor.regions)
        assert changed[1]['box']==[85,70,125,95] and changed[1]['block']==12
        assert output.joinpath('002.png').read_bytes()!=b''
        editor.undo(); assert editor.regions==initial
        editor.redo(); assert editor.regions==changed
        assert view.bind('<Control-z>') and view.bind('<Control-y>') and view.bind('<Control-Shift-Z>')
        # Move selected B with the actual canvas handlers; one drag is one undo operation.
        x,y,scale=editor.placements['modified']
        def event(point): return SimpleNamespace(x=round(x+point[0]*scale),y=round(y+point[1]*scale))
        editor.pointer_down(event((105,82))); editor.pointer_move(event((115,92))); editor.pointer_up(event((115,92)))
        assert editor.regions[1]['box']==[95,80,135,105]
        editor.undo(); assert editor.regions==changed
        # Resize the lower right corner, retaining A/C and selected B's strength.
        editor.selected_id=changed[1]['id']; editor.render()
        editor.pointer_down(event((125,95))); editor.pointer_move(event((140,110))); editor.pointer_up(event((140,110)))
        assert editor.regions[1]['box']==[85,70,140,110] and editor.regions[1]['block']==12
        editor.undo(); assert editor.regions==changed
        # Add, remove, undo and redo a separate manual region through mapped Canvas events.
        editor.tool.set('add'); editor.render()
        p0=event((10,100)); p1=event((40,135))
        editor.canvas.event_generate('<ButtonPress-1>',x=p0.x,y=p0.y)
        editor.canvas.event_generate('<B1-Motion>',x=p1.x,y=p1.y,state=256)
        editor.canvas.event_generate('<ButtonRelease-1>',x=p1.x,y=p1.y)
        assert len(editor.regions)==4 and editor.regions[-1]['box']==[10,100,40,135]
        editor.delete_region(); assert len(editor.regions)==3
        editor.undo(); assert len(editor.regions)==4
        editor.undo(); assert editor.regions==changed
        editor.reset_regions(); assert editor.regions==[]
        editor.undo(); assert editor.regions==changed
        # Damage the saved result, then prove re-save uses only the original and current regions.
        Image.new('RGB',(240,180),'black').save(output/'002.png')
        editor.save_button.invoke(); wait_processing(root,app)
        assert (output/'002.png').read_bytes()!=b''
        editor.close(); app.start(); wait_processing(root,app)
        assert editor.unsaved is False
        assert editor.regions[0]==initial[0] and editor.regions[2]==initial[2]
        with Image.open(output/'002.png') as saved,Image.open(folder/'002.png') as original:
            after=np.asarray(saved); source=np.asarray(original)
            assert np.array_equal(after[30:65,80:115],source[30:65,80:115])
            assert np.array_equal(after[10:40,10:40],baseline[10:40,10:40])
            assert np.array_equal(after[100:150,160:200],baseline[100:150,160:200])
            assert 'parameters' not in saved.info
        assert not set(batch.png_chunk_types(output/'002.png'))&{b'tEXt',b'zTXt',b'iTXt',b'eXIf'}
        for p,(content,mtime) in untouched.items(): assert p.read_bytes()==content and p.stat().st_mtime_ns==mtime
        assert sorted(p.name for p in output.iterdir())==['001.png','002.png','003.png']
        app.clear_inputs(); app.add_paths([folder/'002.png'])
        assert app.documents[0]['detections']==changed
        app.start([0]); wait_processing(root,app)
        for p,(content,mtime) in untouched.items(): assert p.read_bytes()==content and p.stat().st_mtime_ns==mtime
        # Restart uses persisted edit objects; changed originals invalidate the old state.
        clear_root(root); app=prepare_app(root,tmp_path,monkeypatch,detector)
        app.add_paths([folder/'002.png']); assert app.documents[0]['detections']==changed
        Image.new('RGB',(240,180),'white').save(folder/'002.png')
        app.clear_inputs(); app.add_paths([folder/'002.png']); assert app.documents[0]['detections']!=changed
        assert app.documents[0]['detections'][1]['box']==[80,30,115,65]
    finally: clear_root(root)
