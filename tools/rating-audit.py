"""Reproduce deployed v1 without game/network access or synthetic persistence."""
from decimal import Decimal
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.rating import PARAMETERS, WEIGHTS, calculate, displayed

for rounds in (None, 14, 28):
    result = calculate('competitive', 22, 7, 0, rounds)
    divisor = Decimal(rounds or 1)
    print('\nrounds =', rounds, 'tier =', result['tier'])
    factors = []
    for key, value in [('kills',22),('deaths',7),('assists',0)]:
        norm = Decimal(PARAMETERS['competitive'][result['tier']][key])
        rate = Decimal(value) / divisor
        ratio = rate / norm
        factor = Decimal(WEIGHTS[key]) * (ratio - 1) * (-1 if key == 'deaths' else 1)
        factors.append(factor)
        print(key, 'rate',rate,'norm',norm,'ratio',ratio,'centered',ratio-1,'contribution',factor)
    print('raw',1+sum(factors),'stored',result['value'],'display',displayed(result['value']))
print('\nK/D/A | fallback | personal R=14')
for k,d,a in [(4,12,1),(10,12,2),(14,12,4),(17,9,3),(22,7,0),(22,7,1),(25,10,1),(35,10,2),(70,0,0)]:
    print(f'{k}/{d}/{a}',*[str(calculate('competitive',k,d,a,r)['value']) for r in (None,14)])
