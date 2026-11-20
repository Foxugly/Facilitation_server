"""The prod channel layer must not inherit redis-py 8's 5 s socket read timeout.

channels-redis blocks on BZPOPMIN for ``brpop_timeout`` (5 s). With redis-py 8's
default ``socket_timeout=5`` the client gives up at the same moment and raises
"Timeout reading from ...", which kills every idle WebSocket with a 1011.
Tests run on the in-memory layer, so this checks the prod (base) settings.
"""

from channels_redis.core import RedisChannelLayer
from channels_redis.utils import create_pool

from config.settings import base


def test_prod_channel_layer_has_no_socket_read_timeout():
    layer = RedisChannelLayer(**base.CHANNEL_LAYERS["default"]["CONFIG"])
    pool = create_pool(layer.hosts[0])
    assert pool.connection_kwargs.get("socket_timeout", "unset") is None
