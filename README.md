\# EMAIL-CHEK — Conciliação de Reembolsos Jadlog



Sistema desktop para Windows que automatiza a conferência de reembolsos de retenção fiscal: lê e-mails do Outlook, baixa os anexos, extrai os dados dos PDFs (com OCR quando necessário), concilia guia × pagamento Jadlog × reembolso do cliente e aponta as divergências.



\## O que ele faz



\- Lê uma pasta do Outlook (somente leitura — nunca move, marca ou exclui e-mails)

\- Baixa os anexos de cada remessa para uma pasta organizada

\- Classifica cada documento: guia fiscal, comprovante Jadlog, comprovante do cliente, planilha

\- Extrai os campos por OCR ou texto nativo, com cache de layout por formato

\- Concilia as três pontas com score de 0 a 100

\- Registra divergências para revisão manual

\- Exporta tudo para Excel com abas de resumo e divergências



\## Requisitos



\- Windows 10 ou 11

\- Python 3.12 (o `pywin32` não tem build estável para 3.14)

\- Microsoft Outlook instalado e aberto

\- Tesseract OCR instalado (para PDFs escaneados)



\## Instalação



```powershell

python -m venv .venv

.\\.venv\\Scripts\\Activate.ps1

pip install -r requirements.txt

python .venv\\Scripts\\pywin32\_postinstall.py -install

