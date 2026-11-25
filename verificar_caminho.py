from pathlib import Path
from config import PASTA_ANEXOS_PADRAO, BASE_DIR

print("=" * 70)
print("CAMINHO CONFIGURADO x CAMINHO REAL")
print("=" * 70)

print("\n[1] BASE_DIR (pasta do projeto)")
print(f"    {BASE_DIR}")
print(f"    Existe: {BASE_DIR.exists()}")

print("\n[2] PASTA_ANEXOS_PADRAO (o que o app usa)")
p = Path(PASTA_ANEXOS_PADRAO)
print(f"    Valor bruto: {PASTA_ANEXOS_PADRAO!r}")
print(f"    Resolvido:   {p.resolve()}")
print(f"    Absoluto:    {p.is_absolute()}")
print(f"    Existe:      {p.exists()}")

if p.exists():
    subpastas = [d for d in p.iterdir() if d.is_dir()]
    arquivos = [f for f in p.iterdir() if f.is_file()]
    print(f"    Subpastas:   {len(subpastas)}")
    print(f"    Arquivos soltos: {len(arquivos)}")

    print("\n[3] Primeiras 10 subpastas")
    for d in sorted(subpastas)[:10]:
        n = len(list(d.iterdir()))
        print(f"    {d.name}  ({n} itens)")

    print("\n[4] Remessa 18109909021256")
    alvo = p / "18109909021256"
    print(f"    Caminho: {alvo}")
    print(f"    Existe:  {alvo.exists()}")
    if alvo.exists():
        for f in alvo.iterdir():
            print(f"       {f.name} ({f.stat().st_size} bytes)")

    print("\n[5] Total de arquivos em toda a arvore")
    total = 0
    vazias = 0
    for d in subpastas:
        n = len([f for f in d.iterdir() if f.is_file()])
        total += n
        if n == 0:
            vazias += 1
    print(f"    Subpastas vazias: {vazias} de {len(subpastas)}")
    print(f"    Total de arquivos: {total}")

    print("\n[6] Caminho para copiar no Explorer")
    print(f"    {p.resolve()}")
else:
    print("\n    >>> A PASTA NAO EXISTE NO DISCO <<<")
    print("    Criando...")
    p.mkdir(parents=True, exist_ok=True)
    print(f"    Criada: {p.exists()}")

print("\n" + "=" * 70)
