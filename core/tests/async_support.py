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
