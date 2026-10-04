import time
from types import SimpleNamespace
from PIL import Image
from tkinterdnd2 import TkinterDnD
from desktop import App, validate_settings
import batch
import pytest

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
    folder=tmp_path/'日本語 フォルダ'; folder.mkdir()
    Image.new('RGB',(600,800),'blue').save(folder/'test.png')
    root=shared_root
    args=SimpleNamespace(input=None,output=None,no_open=True,include_nipple=False,confidence=.25,padding=10,block=20)
    try:
        app=App(root,args)
        root.geometry('1060x780+4000+4000')
        app.on_drop(SimpleNamespace(data='{'+str(folder)+'}'))
        assert app.folder==folder and '未処理' in app.status.get()
        assert not folder.with_name(folder.name+'-censored').exists()
        app.start()
        assert app.busy and '処理中' in app.status.get()
        deadline=time.monotonic()+30
        while app.busy and time.monotonic()<deadline:
            root.update(); time.sleep(.02)
        assert not app.busy and '処理・保存完了' in app.status.get()
        assert app.report['output']==str(folder.with_name(folder.name+'-censored'))
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
        assert sorted(p.name for p in (folder.with_name(folder.name+'-censored')).iterdir())==['test.png']
    finally:
        clear_root(root)

def test_settings_persist_validate_and_reach_processing(tmp_path,monkeypatch,shared_root):
    import pytest
    monkeypatch.setattr(batch,'ROOT',tmp_path)
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
        assert app.report['log_file'] and (folder.with_name('input-censored.processing.json')).exists()
        settings=app.open_settings()
        assert settings.winfo_exists()
        settings.destroy()
    finally: clear_root(root)
