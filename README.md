# QuantBot

> El mercado no premia el esfuerzo. Premia la precision.
> Archon Consultancies

---

## Que es

Sistema de trading algoritmico de codigo abierto.
Elimina la variable humana de la ejecucion financiera.

---

## Stack

- yfinance + NewsAPI: datos de mercado y noticias reales
- XGBoost: modelo predictivo de direccion del precio
- Kelly Criterion: sizing optimo de posicion
- Alpaca API: paper trading y ejecucion real
- schedule: loop automatico cada hora

---

## Parametros de control

- Exposicion maxima por posicion: 5%
- Drawdown maximo: 10%
- Confianza minima para ejecutar: 60%
- Capital inicial simulado: 10.000 USD

---

## Instalacion

git clone https://github.com/palcocerhurtado-tech/quantbot.git
cd quantbot
pip3 install -r requirements.txt
python3 setup_project.py
python3 main.py

---

## Advertencia

Opera en paper trading por defecto.
Ningun sistema garantiza rentabilidad.
Audita el codigo antes de conectar capital real.

---

## Filosofia Archon

El trabajo duro no sirve de nada si el sistema esta roto.
Reemplazamos la fragilidad humana por infraestructuras algoritmicas que no fallan.

---

Pablo Alcocer — Fundador Archon Consultancies
github.com/palcocerhurtado-tech/quantbot
