# Video de respaldo (plan C)

Un video de unos 6 minutos con la misma historia de [guion-demo.md](guion-demo.md), por si en la presentación fallan la red, Docker o el equipo.

## Preparación (5 minutos)

1. Cerrar notificaciones: **Configuración → Sistema → Notificaciones → No molestar**.
2. Levantar y dejar en cero:
   ```bash
   docker compose up -d
   powershell -ExecutionPolicy Bypass -File scripts/reset_demo.ps1
   ```
3. Abrir **http://localhost:8501** en el navegador, en pantalla completa (**F11**), con zoom al 110 % (**Ctrl +**) para que se lea bien en video.
4. Tener a mano, en el Explorador de archivos, la carpeta `data/samples` (para adjuntar los PDF) y el PDF `oc-inyeccion.pdf` abierto en otra pestaña (para el zoom a la letra escondida).

## Grabar con la herramienta de Windows

- **Win + G** abre la barra de juegos; la primera vez, aceptar.
- **Win + Alt + R** empieza y termina la grabación de la ventana activa (el navegador).
- El video queda en **Videos → Capturas**.
- Para grabar la voz: en la barra de juegos, activar el micrófono antes de empezar.

Alternativa con más control (varias ventanas, cortes): **OBS Studio** (gratuito), fuente "Captura de ventana".

## Durante la grabación

- Seguir el guion **copiando y pegando** los textos: salen idénticos al ensayo desde la caché (la pantalla indica "Respuesta desde la caché").
- Narrar lo mismo que en vivo; frases clave en el guion.
- Si algo sale distinto: detener (**Win + Alt + R**), correr `reset_demo.ps1` y volver a empezar.

## Después

- Revisar el video completo una vez.
- Copiarlo a un pendrive **y** a la nube (dos lugares distintos del equipo de la presentación).
