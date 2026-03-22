# ARCHON CONSULTANCIES

> Precision. Control. Arquitectura.

## Estructura Del Proyecto

Este repositorio mezcla dos piezas distintas:

`index.html`
: Landing principal y canónica de Archon Consultancies.

`archon.html`
: Variante experimental de la misma marca. No es la página principal.

`quantbot/`
: Copia del proyecto QuantBot con su propia estructura de ejecución.

## Archon Consultancies

Archon es una consultoria de infraestructuras algoritmicas para e-commerce. El objetivo es reducir trabajo manual en operaciones criticas y convertir procesos repetitivos en flujos automatizados, auditables y medibles.

## Arquitectura Archon

- `La Caja Fuerte` (`Airtable`): inventario blindado en base de datos relacional externa.
- `Las Arterias` (`Make` / `n8n`): tienda, banco y almacen conectados en milisegundos.
- `La Aduana de Seguridad` (`Stripe`): ningun pedido avanza sin confirmacion de pago.
- `El Supervisor` (`IA`): auditoria de direcciones y deteccion automatica de anomalias.

## Servicios

- Auditoria Operativa gratuita de 33 minutos.
- Radiografia Operativa: 250 euros.
- Setup Logistica Express: 950 euros.
- Full Stack Cerebro Archon: 2.500 euros.
- Mantenimiento y Calidad Total: 350 euros / mes.

## QuantBot

QuantBot es el proyecto open source de trading algoritmico del repositorio. Usa datos de mercado, features tecnicas, sentimiento y un modelo predictivo para generar señales y ejecutar operaciones en modo controlado.

## Puesta En Marcha

```bash
pip3 install -r requirements.txt
python3 main.py
```

## Notas

- La landing principal es `index.html`.
- `archon.html` queda como variante experimental.
- El bot depende de claves y variables de entorno para los servicios externos.
