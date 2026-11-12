import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import audio, microphone
from esphome.const import CONF_ID, CONF_SAMPLE_RATE, CONF_BITS_PER_SAMPLE, CONF_NUM_CHANNELS
from .text_sensor import AmazonRemote, amazon_remote_ns

DEPENDENCIES = ["ble_client"]
AUTO_LOAD = ["audio"]

AmazonRemoteMicrophone = amazon_remote_ns.class_(
    "AmazonRemoteMicrophone", cg.Component, microphone.Microphone
)


def set_stream_limits(config):
    audio.set_stream_limits(
        min_bits_per_sample=16, max_bits_per_sample=16,
        min_channels=1, max_channels=1,
        min_sample_rate=16000, max_sample_rate=16000,
    )(config)
    return config


CONFIG_SCHEMA = cv.All(
    microphone.MICROPHONE_SCHEMA.extend({
        cv.GenerateID(): cv.declare_id(AmazonRemoteMicrophone),
        cv.Required("remote_id"): cv.use_id(AmazonRemote),
        cv.Optional(CONF_SAMPLE_RATE, default=16000): cv.one_of(16000, int=True),
        cv.Optional(CONF_BITS_PER_SAMPLE, default=16): cv.one_of(16, int=True),
        cv.Optional(CONF_NUM_CHANNELS, default=1): cv.one_of(1, int=True),
    }).extend(cv.COMPONENT_SCHEMA),
    set_stream_limits,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await microphone.register_microphone(var, config)
    remote = await cg.get_variable(config["remote_id"])
    cg.add(var.set_remote(remote))
    audio.request_opus_support()
