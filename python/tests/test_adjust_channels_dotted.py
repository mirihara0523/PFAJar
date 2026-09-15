from pathlib import Path
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'py'))
from adjust_channels import lowres_channels_for_slice

with tempfile.TemporaryDirectory() as t:
    root=Path(t); (root/'202607.M554.M579.01.63.png').write_bytes(b'png')
    channels=lowres_channels_for_slice(root,'202607',None)
    assert channels == [('DAPI (pipeline)', root/'202607.M554.M579.01.63.png')]
    assert lowres_channels_for_slice(root,'202608',None) == []
print('PASS dotted DAPI fallback discovery')
