"""Base de las pruebas asíncronas de la UI.

unittest.IsolatedAsyncioTestCase crea siempre su bucle con asyncio en modo depuración:
cada callback lento se registra («Executing <Task …> took 0.3 seconds») y se rastrea
el origen de cada corrutina. Con Textual eso multiplica la duración de la suite sin
aportar nada a las aserciones, así que se desactiva al empezar cada prueba.
"""
import asyncio
import unittest


class AsyncTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        await super().asyncSetUp()


async def settle(app, pilot):
    """Espera a que un clic termine: mensajes, workers que lanzó y su repintado.

    Comprobar tras un único pilot.pause() depende de la velocidad del bucle; con el
    modo depuración la lentitud lo ocultaba."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def until(pilot, condition, timeout=5.0):
    """Repinta a intervalos hasta que se cumpla la condición o venza el plazo.

    Sirve cuando un worker abre un diálogo y sigue esperando su respuesta: ahí
    wait_for_complete no volvería nunca. Devuelve si se cumplió; la aserción posterior
    da el mensaje del fallo."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not condition():
        if loop.time() > end:
            return False
        await pilot.pause(0.02)
    return True


async def click(pilot, target, timeout=5.0):
    """pilot.click cuando el widget ya es alcanzable en el mapa de pantalla.

    pilot.click localiza el destino con el compositor, que sólo se actualiza al
    repintar; tras un scroll o un diálogo nuevo puede seguir con la disposición
    anterior y el clic cae en otro widget. Además, un Button pulsado hace menos de
    ~0,2 s descarta el clic aunque lo reciba."""
    from textual.widget import Widget
    app = pilot.app
    widget = target if isinstance(target, Widget) else app.screen.query_one(target)

    def reachable():
        # A Button ignores clicks while its pressed effect (-active, ~0.2 s) lasts.
        if widget.has_class('-active'):
            return False
        region = widget.region
        if not region.area:
            return False
        try:
            found, _ = app.get_widget_at(region.x, region.y)
        except Exception:
            return False
        # Composite widgets (Select, Checkbox…) receive the click through a child.
        return found is widget or widget in found.ancestors

    await until(pilot, reachable, timeout)
    return await pilot.click(widget)
