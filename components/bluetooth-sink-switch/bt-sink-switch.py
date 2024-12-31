#!/usr/bin/env python3
"""
Provides bt_switch (see below) as function and callable script

If called as script, the configuration of led_pin / led_pin2 reflecting audio sink status is read from ../../settings/gpio_settings.ini'
See function get_led_pin_configuration for details. If no configuration file is found led_pin is None

Usage:
$ bt-sink-switch cmd [debug]
    cmd = toggle|speakers|headphones|toggle2|headphones2 : select audio target
    debug                            : enable debug logging
"""

import sys
import re
import subprocess
import logging
import os
import configparser
import time


# Create logger
logger = logging.getLogger('bt-sink-switch.py')
logger.setLevel(logging.DEBUG)
# Create console handler and set default level
logconsole = logging.StreamHandler()
logconsole.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s: %(message)s', datefmt='%d.%m.%Y %H:%M:%S'))
logconsole.setLevel(logging.INFO)
logger.addHandler(logconsole)


def bt_usage(sname):
    """Print usage, if module is called as script"""
    print("Usage")
    print("  ./" + sname + " toggle | speakers | headphones | toggle2 | headphones2 [debug]")


def bt_check_mpc_err() -> None:
    """Error check on mpd output stream and attempt to recover previous state"""
    logger.debug("bt_check_mpc_err()")
    mpcproc = subprocess.run("mpc status", shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    logger.debug(mpcproc.stdout)
    # grep for this expression: 'ERROR: Failed to open audio output'
    mpcerr = re.search(b"ERROR:.*output", mpcproc.stdout)
    if mpcerr is not None:
        mpcplay = subprocess.run("mpc play", shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        logger.debug(mpcplay)

def bt_led(led_pin, isEnabled):
    if led_pin is None:
        return
    proc = subprocess.run(["gpioset", led_pin[0], led_pin[1]+"=" +( "1" if isEnabled else "0")], shell=False,
                          check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    msg = "LED "+("on" if isEnabled else "off")+": "
    logger.debug(msg.encode("utf-8") + proc.stdout)

def bt_leds(led_pins, led_states):
    for i in range(len(led_pins)):
        bt_led(led_pins[i], led_states[i])

def bt_switch_to(output_id, led_pins, led_states):
    print(f"Switched audio sink to \"Output {output_id}\"")
    # With mpc enable only 2, output 1 gets disabled before output 2 gets enabled causing a stream output fail
    # This order avoids the issue
    # old: disable of output 1, but we need to disable all
    print(f"mpc enable {output_id}; sleep 0.1; mpc enable {output_id} only")
    proc = subprocess.run(["mpc","enable", str(output_id)], shell=False, check=False,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    logger.debug(proc.stdout)
    
    time.sleep(0.1)
    
    proc = subprocess.run(["mpc","enable", "only", str(output_id)], shell=False, check=False,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    logger.debug(proc.stdout)
    # Yet, in some cases, a stream error still occurs: check and recover
    bt_check_mpc_err()
    bt_leds(led_pins, led_states)

def bt_find_led_pin(led_pin):
    # detect GPIO
    proc = subprocess.run(["gpiofind", led_pin], 
                            shell=False, check=False,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    logger.debug(proc.stdout)
    if proc.returncode != 0:
        logger.error("GPIO for LED not found")
        return None
    else:
        return proc.stdout.decode("utf-8").strip().split(" ")

def bt_connect_all():
    btDevices_console = subprocess.run("bluetoothctl devices", shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    logger.debug(btDevices_console.stdout)
    for line in btDevices_console.stdout.decode("utf-8").strip().split("\n"):
        r = re.search("^Device ([0-9A-F:]*) ", line)
        if r is None:
            continue
        device = r.group(1)
        c = subprocess.run(["bluetoothctl","connect",device], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        logger.debug(c.stdout)

def bt_switch(cmd, led_pins): # noqa C901
    """
    Set/Toggle between regular speakers and headphone output. If no bluetooth device is connected,
    always defaults to mpc output 1

    To be precise: toggle between mpc output 1 and mpc output 2.

    So, set up your /etc/mpd.conf correctly: first audio_output section should be speakers,
    second audio_output section should be headphones
    To set up bluetooth headphones, follow the wiki
    Short guide to connect bluetooth (without audio setup)
        sudo bluetoothctl
        power on
        agent on
        scan on                   -> shows list of Bluetooth devices in reach
        pair C4:FB:20:63:A7:F2    -> pairing happens
        trust C4:FB:20:63:A7:F2   -> trust you device
        connect C4:FB:20:63:A7:F2 -> connect
        scan off
        exit
    Next time headphones are switched on, they should connect automatically

    Requires
      sudo apt install bluetooth

    Attention
      The user to runs this script (precisly who runs bluetoothctl) needs proper access rights.
      Otherwise bluetoothctl will always return "no default controller found"
      The superuser and users of group "bluetooth" have these. You can check the policy here
        /etc/dbus-1/system.d/bluetooth.conf
      Best to check first if the user which later runs this script can execute bluetoothctl and get meaningful results
        sudo -u www-data bluetoothctl show
        E.g. if you want to do bluetooth manipulation from the web interface, you will most likely need to add www-data
        to the group bluetooth
             if you want to test this script from the command line, you will most likely need to add user pi
             (or whoever you are) to the group bluetooth or run it as superuser
        sudo usermod -G bluetooth -a www-data
      Don't forget to reboot for group changes to take effect here

    LED support
      If LED number (GPIO number, BCM) is provided, a LED is switched to reflect output sink status
      off = speakers, on = headphones
      LED blinks if no bluetooth device is connected and bluetooth sink is requested, before script default to output 1

      A note for developers: This script is not persistent and only gets called (from various sources)
      when the output sink is changed/toggled and exits.
        This is done to make is callable from button press (gpio button handler), rfid card number, web interface
        The LED state however should be persistent. With GPIOZero, the LED state gets reset at the end of the script.
        For that reason GPIO state is manipulated through shell commands

    Parameters
    ----------
    :param cmd: string is "toggle" | "speakers" | "headphones" | "toggle2" | "headphones2"
    :param led_pin / led_pin2: GPIO pin number of LED to reflect output status. If None, LED support is disabled
    (and no GPIO pin is blocked)
    """
    # Check for valid command
    if cmd != "toggle" and cmd != "speakers" and cmd != "headphones" and cmd != "toggle2" and cmd != "headphones2":
        logger.error("Invalid command. Doing nothing.")
        return

    # Rudimentary check if LED pin request is valid GPIO pin number
    led_pins = [ bt_find_led_pin(led_pin) for led_pin in led_pins ]

    # Figure out if output 1 (speakers) is enabled
    isOutputsOn_console = subprocess.run("mpc outputs", shell=True, check=False, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)
    logger.debug(isOutputsOn_console.stdout)
    isOutput2On = re.search(b"Output 2.*enabled", isOutputsOn_console.stdout)
    isOutput3On = re.search(b"Output 3.*enabled", isOutputsOn_console.stdout)

    if (cmd == "toggle" and not isOutput2On) or (cmd == "headphones"):
        # command to turn on bluetooth output

        # Figure out if a bluetooth device is connected (any device will do). Assume here that only speakers/headsets
        # will be connected
        # -> No need for user to adapt MAC address
        # -> will actually support multiple speakers/headsets paired to the phoniebox
        # Alternative: Check for specific bluetooth device only with "bluetoothctl info MACADDRESS"
        isBtConnected_console = subprocess.run("bluetoothctl info", shell=True, check=False, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT)
        logger.debug(isBtConnected_console.stdout)
        isBtConnected = re.search(b"Connected:\s+yes", isBtConnected_console.stdout) # noqa W605

        # Only switch to BT headphones if they are actually connected
        if isBtConnected:
            bt_switch_to(2, led_pins, [True, False])
            return
        else:
            print("No bluetooth device connected. Defaulting to \"Output 1\".")
            bt_connect_all()

            sleeptime = 0.25
            for i in range(0, 3):
                bt_leds(led_pins, [True, False])
                time.sleep(sleeptime)
                bt_leds(led_pins, [False, False])
                time.sleep(sleeptime)
    elif (cmd == "toggle2" and not isOutput3On) or (cmd == "headphones2"):
        # command to turn on bluetooth output 3
        bt_switch_to(3, led_pins, [False, True])
        return

    # Default: Switch to Speakers
    bt_switch_to(1, led_pins, [False, False])

def get_led_pin_config(cfg_file):
    """Read the led pin for reflecting current sink status from cfg_file which is a Python configparser file

    cfg_file is relative to this script's location or an absolute path

    The file must contain the entry

    [BluetoothToggleLed]
    led_pin: GPIO27
    led_pin2: GPIO17

    where
    - led_pin is the BCM number of the GPIO pin (i.e. 'led_pin = GPIO6' means GPIO6) and defaults to None
    - led_pin2 is the BCM number of the GPIO pin (i.e. 'led_pin = GPIO6' means GPIO6) and defaults to None used for Output 3

    Note: Capitalization of [BluetoothToggleLed] is important!"""

    # Make sure to locate cfg_file relative to this script's location independent of working directory
    if not os.path.isabs(cfg_file):
        cfg_file = os.path.dirname(os.path.realpath(__file__)) + '/' + cfg_file
    logger.debug(f"Reading config file: '{cfg_file}'")
    cfg = configparser.ConfigParser()
    cfg_file_success = cfg.read(cfg_file)
    if not cfg_file_success:
        logger.debug(f"Could not read '{cfg_file}'. Continue with default values (i.e. led off).")

    section_name = 'BluetoothToggleLed'
    led_pin = None
    led_pin2 = None
    if section_name in cfg:
        led_pin = cfg[section_name].get('led_pin', fallback=None)
        led_pin2 = cfg[section_name].get('led_pin2', fallback=None)
        if not led_pin:
            logger.warning("Could not find 'led_pin'")
            led_pin = None
        if not led_pin2:
            logger.warning("Could not find 'led_pin2'")
            led_pin2 = None
    else:
        logger.debug(f"No section {section_name} found. Defaulting to led_pin = None")

    logger.debug(f"Using LED pin = {led_pin}, {led_pin2}")
    return [led_pin, led_pin2]


if __name__ == "__main__":
    if len(sys.argv) == 3:
        logconsole.setLevel(logging.DEBUG)

    if 2 <= len(sys.argv) <= 3:
        cfg_led_pin = get_led_pin_config('../../settings/gpio_settings.ini')
        bt_switch(sys.argv[1], cfg_led_pin)
    else:
        bt_usage(sys.argv[0])
