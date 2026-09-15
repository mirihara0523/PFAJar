from pathlib import Path
source=(Path(__file__).resolve().parents[2]/'py'/'adjust.py').read_text(encoding='utf-8')
assert 'QSpinBox::up-button' not in source
assert 'QSpinBox::down-button' not in source
print('PASS ring thickness controls use default vertical positions')
