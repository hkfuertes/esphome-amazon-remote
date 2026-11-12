import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import ble_client, text_sensor

DEPENDENCIES = ["ble_client"]

amazon_remote_ns = cg.esphome_ns.namespace("amazon_remote")
AmazonRemote = amazon_remote_ns.class_(
    "AmazonRemote", cg.Component, ble_client.BLEClientNode, text_sensor.TextSensor
)

CONFIG_SCHEMA = (
    text_sensor.text_sensor_schema(AmazonRemote)
    .extend(ble_client.BLE_CLIENT_SCHEMA)
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = await text_sensor.new_text_sensor(config)
    await cg.register_component(var, config)
    await ble_client.register_ble_node(var, config)
