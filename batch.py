"""Local PNG batch using the MIT-licensed dghs-imgutils detector."""
import os, sys
from pathlib import Path
ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
RESOURCES = Path(getattr(sys, '_MEIPASS', ROOT))
os.environ['HF_HOME'] = str(RESOURCES / 'cache' / 'huggingface')
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['ONNX_MODE'] = 'cpu'
import argparse, json, math, shutil, time, traceback
import numpy as np
from PIL import Image, PngImagePlugin
from imgutils.detect import detect_censors


def expand_region(box, margin, width, height):
    """Add the chosen margin and intersect a detection with the image bounds."""
    limits=np.array([width,height,width,height],dtype=np.int64)
    corners=np.asarray(box,dtype=np.int64)+np.array([-margin,-margin,margin,margin],dtype=np.int64)
    region=np.clip(corners,0,limits)
    if np.any(region[2:]<=region[:2]):
        raise ValueError('検出領域が画像内の有効な矩形になりません。')
    return tuple(int(coordinate) for coordinate in region)


def pixelate(image, boxes, block):
    result = image.copy()
    for box in boxes:
        crop = image.crop(box)
        tiny = crop.resize((max(1, math.ceil(crop.width/block)), max(1, math.ceil(crop.height/block))), Image.Resampling.BOX)
        region = tiny.resize(crop.size, Image.Resampling.NEAREST)
        if image.mode in ('RGBA', 'LA'):
            region.putalpha(crop.getchannel('A'))
        result.paste(region, box)
    return result


def process(src, out, args, detector=detect_censors):
    with open(src,'rb') as stream:
        header=stream.read(26)
    if len(header)<26 or header[:8]!=b'\x89PNG\r\n\x1a\n' or header[24]>8:
        raise ValueError('Only 8-bit or lower PNG is supported; 16-bit PNG is rejected without modification.')
    with Image.open(src) as im:
        if im.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P') or getattr(im, 'n_frames', 1) != 1:
            raise ValueError('Supported: single-frame 8-bit RGB/RGBA/L/LA/palette PNG. Source unchanged.')
        meta = im.info.copy()
        # No EXIF rotation or whole-image resizing. Palette images preserve decoded RGBA values.
        image = im.convert('RGBA') if im.mode == 'P' or 'transparency' in meta else im.copy()
    labels = {'pussy', 'penis'} | ({'nipple_f'} if args.include_nipple else set())
    raw = detector(image.convert('RGB'), level='s', conf_threshold=args.confidence)
    dets = [dict(box=list(expand_region(b, args.padding, *image.size)), label=l, confidence=float(c)) for b,l,c in raw if l in labels]
    boxes = [d['box'] for d in dets]
    result = pixelate(image, boxes, args.block)
    mask = np.zeros((image.height, image.width), dtype=bool)
    for x0,y0,x1,y1 in boxes:
        mask[y0:y1,x0:x1] = True
    before = np.asarray(image)
    if boxes:
        pnginfo = PngImagePlugin.PngInfo()
        for k,v in meta.items():
            if isinstance(v, str): pnginfo.add_itxt(k,v)
        kwargs = {k:meta[k] for k in ('icc_profile','dpi','exif') if k in meta}
        result.save(out, pnginfo=pnginfo, **kwargs)
    else:
        shutil.copy2(src,out)
    with Image.open(out) as saved:
        saved = saved.convert('RGBA') if saved.mode == 'P' or 'transparency' in saved.info else saved.copy()
    if saved.size != image.size or saved.mode != image.mode or not np.array_equal(before[~mask], np.asarray(saved)[~mask]):
        out.unlink(missing_ok=True)
        raise RuntimeError('Output verification failed: size or pixels outside mask changed.')
    if image.mode in ('RGBA','LA') and not np.array_equal(np.asarray(image.getchannel('A')), np.asarray(saved.getchannel('A'))):
        out.unlink(missing_ok=True)
        raise RuntimeError('Alpha verification failed.')
    return dict(file=src.name, size=list(image.size), status='検出あり・要確認' if boxes else '検出なし・要確認', detections=dets, outside_pixels_equal=True), result


def output_folder(folder):
    candidate = folder.with_name(folder.name + '-censored')
    number = 2
    while candidate.exists():
        candidate = folder.with_name(folder.name + f'-censored-{number}')
        number += 1
    return candidate


def run(args, progress=None):
    folder = Path(args.input).resolve()
    if not folder.is_dir(): raise ValueError('Input folder does not exist.')
    images = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower()=='.png'])
    if not images: raise ValueError('No PNG files directly in the selected folder.')
    out = Path(args.output).resolve() if args.output else output_folder(folder)
    if out == folder or out.exists(): raise ValueError('Output must be a new folder separate from input.')
    out.mkdir(parents=True)
    entries = []
    start = time.perf_counter()
    for i,src in enumerate(images,1):
        if progress: progress(i-1, len(images), src.name)
        print(f'[{i}/{len(images)}] {src.name}', flush=True)
        try:
            entry, result = process(src, out/src.name, args)
        except Exception as exc:
            traceback.print_exc()
            entry = dict(file=src.name,status='エラー',error=str(exc))
        entries.append(entry)
    report = dict(input=str(folder), output=str(out), seconds=round(time.perf_counter()-start,2), settings=vars(args), images=entries)
    errors = sum(e['status']=='エラー' for e in entries)
    if getattr(args, 'save_log', False):
        log_path=out.with_name(out.name+'.processing.json')
        number=2
        while log_path.exists():
            log_path=out.with_name(out.name+f'.processing-{number}.json'); number+=1
        report['log_file']=str(log_path)
        try:
            with log_path.open('x',encoding='utf-8') as stream:
                json.dump(report,stream,ensure_ascii=False,indent=2)
        except OSError as exc:
            report['log_error']=str(exc)
    print(f'Output: {out}\nElapsed: {report["seconds"]} seconds. Errors: {errors}',flush=True)
    if not args.no_open: os.startfile(str(out))
    if progress: progress(len(images), len(images), None, report)
    return 1 if errors else 0


def main():
    parser=argparse.ArgumentParser(description='Folder batch PNG mosaic with verified outside-pixel preservation.')
    parser.add_argument('input',nargs='?')
    parser.add_argument('--output')
    parser.add_argument('--confidence',type=float,default=.25)
    parser.add_argument('--padding',type=int,default=10)
    parser.add_argument('--block',type=int,default=20)
    parser.add_argument('--include-nipple',action='store_true')
    parser.add_argument('--save-log',action='store_true')
    parser.add_argument('--no-open',action='store_true')
    parser.add_argument('--check-ui',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--licenses',action='store_true')
    args=parser.parse_args()
    if args.licenses:
        from desktop import show_licenses
        return show_licenses()
    if not 0<=args.confidence<=1 or args.padding<0 or args.block<2: parser.error('Invalid confidence/padding/block.')
    if args.check_ui:
        from desktop import check_ui
        return check_ui(args)
    if not args.input or not args.no_open:
        from desktop import launch
        return launch(args)
    return run(args)

if __name__=='__main__':
    # Windowed builds have no console streams. Keep diagnostics in memory.
    if sys.stdout is None:
        import io
        sys.stdout=io.StringIO()
        sys.stderr=io.StringIO()
    try: sys.exit(main())
    except Exception:
        traceback.print_exc(); sys.exit(1)
