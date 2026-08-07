from decimal import *
elements = {"H":Decimal('1.0080'), "He":Decimal('4.0026'), "Li":Decimal('6.94'), "Be":Decimal('9.0122'), "B":Decimal('10.81'), "C":Decimal('12.011'), "N":Decimal('14.007'), "O":Decimal('15.999'), "F":Decimal('18.998'), "Ne":Decimal('20.180'), "Na":Decimal('22.990'), "Mg":Decimal('24.305'), "Al":Decimal('26.982'), "Si":Decimal('28.085'), "P":Decimal('30.974'), "S":Decimal('32.06'), "Cl":Decimal('35.45'), "Ar":Decimal('39.95'), "K":Decimal('39.098'), "Ca":Decimal('40.078'), "Sc":Decimal('44.956'), "Ti":Decimal('47.867'), "V":Decimal('50.942'), "Cr":Decimal('51.996'), "Mn":Decimal('54.938'), "Fe":Decimal('55.845'), "Co":Decimal(' 58.933'), "Ni":Decimal('58.693'), "Cu":Decimal('63.546'), "Zn":Decimal('65.38'), "Ga":Decimal('69.723'), "Ge":Decimal('72.630'), "As":Decimal('74.922'), "Se":Decimal('78.971'), "Br":Decimal('79.904'), "Kr":Decimal('83.798'), "Rb":Decimal('85.468'), "Sr":Decimal('87.62'), "Y":Decimal('88.906'), "Zr":Decimal('91.222'), "Nb":Decimal('92.906'), "Mo":Decimal('95.95'), "Rh":Decimal('102.91'), "Pd":Decimal('106.42'), "Ag":Decimal('107.87'), "Cd":Decimal('112.41'), "In":Decimal('114.82 '), "Sn":Decimal('118.71'), "Sb":Decimal('121.76'), "Te":Decimal('127.60'), "I":Decimal('126.90'), "Xe":Decimal('131.29'), "Cs":Decimal('132.91'), "Ba":Decimal('137.33'), "La":Decimal('138.91'), "Ce":Decimal('140.12'), "Pr":Decimal('140.91'), "Nd":Decimal('144.24'), "Sm":Decimal('150.36'), "Eu":Decimal('151.96'), "Gd":Decimal('157.25'), "Tb":Decimal('158.93'), "Dy":Decimal('162.50'), "Ho":Decimal('164.93'), "Er":Decimal('167.26'), "Tm":Decimal('168.93'), "Yb":Decimal('173.05'), "Lu":Decimal('174.97'), "Hf":Decimal('178.49'), "Ta":Decimal('180.95'), "W":Decimal('183.84'), "Re":Decimal('186.21'), "Os":Decimal('190.23'), "Ir":Decimal('192.22'), "Pt":Decimal('195.08'), "Au":Decimal('196.97'), "Hg":Decimal('200.59'), "Tl":Decimal('204.38'), "Pb":Decimal('207.2'), "Bi":Decimal('208.98'), "Th":Decimal('232.04'), "Pa":Decimal('231.04'), "U":Decimal('238.03')}

# the contents of "Added Mass" goes here
target = Decimal('266')

for a in elements:
    for b in elements:
        if elements[a]+elements[b] == target:
            print(a,b)
        for c in elements:
            if elements[a]+elements[b]+elements[c] == target:
                print(a,b,c)