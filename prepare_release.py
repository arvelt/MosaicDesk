"""Pin the model and preserve upstream third-party notices in the release."""
from pathlib import Path
import hashlib, importlib.metadata as metadata, os, sys

ROOT=Path(__file__).resolve().parent
REVISION='0cf62fd6b28213b40ae0c0055f92e7ae6a96bdc2'
MODEL_SHA256='2c2524824d7d320c5619a0a73702a2e2186f619067c823d211e94f7cfe489cba'
os.environ['HF_HOME']=str(ROOT/'cache'/'huggingface')
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
from huggingface_hub import hf_hub_download

def main():
    model=Path(hf_hub_download('deepghs/anime_censor_detection','censor_detect_v1.0_s/model.onnx',revision=REVISION))
    if hashlib.sha256(model.read_bytes()).hexdigest()!=MODEL_SHA256:
        raise RuntimeError('Pinned model SHA256 mismatch.')
    paragraphs=['MosaicDesk: third-party copyright and license notices\nThese notices apply to their respective components, independently of the application MIT license.\nSupplementary notices for build/test dependencies are also retained.']
    missing=[]
    for distribution in sorted(metadata.distributions(),key=lambda d:d.metadata['Name'].lower()):
        name=distribution.metadata['Name']
        license_files=[f for f in distribution.files or [] if Path(str(f)).name.upper().startswith(('LICENSE','COPYING','NOTICE')) and '.py' not in Path(str(f)).suffix]
        texts=[]
        for f in license_files:
            path=distribution.locate_file(f)
            if path.is_file():
                texts.append(f'--- {f} ---\n'+path.read_text(encoding='utf-8',errors='replace'))
        expression=distribution.metadata.get('License-Expression') or distribution.metadata.get('License','Not specified in package metadata')
        paragraphs.append(f'\n{"="*72}\n{name} {distribution.version}\nLicense metadata: {expression}\n'+ '\n'.join(texts))
        if not texts: missing.append(name)
    import urllib.request
    supplements={
        'flatbuffers':'https://raw.githubusercontent.com/google/flatbuffers/master/LICENSE',
        'random-user-agent':'https://raw.githubusercontent.com/Luqman-Ud-Din/random_user_agent/master/LICENSE',
        'tokenizers':'https://raw.githubusercontent.com/huggingface/tokenizers/main/LICENSE',
        'tqdm':'https://raw.githubusercontent.com/tqdm/tqdm/master/LICENCE',
        'URLObject':'https://raw.githubusercontent.com/zacharyvoase/urlobject/master/UNLICENSE',
    }
    for name in missing:
        if name not in supplements: raise RuntimeError('License text missing for '+name)
        url=supplements[name]
        with urllib.request.urlopen(url,timeout=30) as response:
            text=response.read().decode('utf-8')
        paragraphs.append('\nSupplementary upstream notice for '+name+'\nSource: '+url+'\n'+text)
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists(): paragraphs.append('\nPython runtime\n'+python_license.read_text(encoding='utf-8',errors='replace'))
    for directory in ('tcl8.6','tk8.6'):
        license_file=Path(sys.base_prefix)/'tcl'/directory/'license.terms'
        if license_file.exists(): paragraphs.append('\n'+directory+'\n'+license_file.read_text(encoding='utf-8',errors='replace'))
    model_license=(ROOT/'LICENSE').read_text(encoding='utf-8').split('Permission is hereby granted',1)[1]
    paragraphs.append('\n'+ '='*72+'\nModel: deepghs/anime_censor_detection / censor_detect_v1.0_s\nProvider: DeepGHS\nRevision: '+REVISION+'\nSHA256: '+MODEL_SHA256+'\nSource: https://huggingface.co/deepghs/anime_censor_detection\nUpstream model card declares: license: mit\nNo separate copyright-year notice was provided in the model card.\nMIT license terms:\nPermission is hereby granted'+model_license)
    # tkdnd binary notices are shipped as package data; retain their exact notices here as well.
    import tkinterdnd2
    tkdnd=Path(tkinterdnd2.__file__).parent/'tkdnd'/'win-x64'
    for path in tkdnd.iterdir():
        if path.is_file() and path.name.lower().startswith(('license','pkgindex','tkdnd')) and path.suffix!='.dll':
            text=path.read_text(encoding='utf-8',errors='replace')
            if 'Copyright' in text or 'copyright' in text: paragraphs.append('\ntkdnd / '+path.name+'\n'+text)
    (ROOT/'THIRD_PARTY_NOTICES.txt').write_text('\n'.join(paragraphs),encoding='utf-8')
    print('Model checksum verified. Notices collected. Packages without separate license text:', ', '.join(missing))

if __name__=='__main__': main()
