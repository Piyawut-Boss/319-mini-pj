# Stable Arduino device path (udev rule)

`app.py` connects to the door-guard Arduino at `/dev/arduino_rfid` — a
symlink created by udev, not a raw `/dev/ttyACM0` path. Without this, the
board re-enumerating (USB reset, replug) can silently rename the device
node mid-session and the connection goes stale with no obvious error.

## Install (one-time, on the Pi)

```bash
sudo cp 99-arduino-rfid.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Check it worked:
```bash
ls -la /dev/arduino_rfid
```

## If the board is ever swapped for a different physical Uno

The rule matches this specific board's USB serial number. Plug the new
board in, find its serial, and update the rule:

```bash
udevadm info -a -n /dev/ttyACM0 | grep -E 'idVendor|idProduct|serial'
```

Replace the `serial` value in `99-arduino-rfid.rules` with the new one,
then re-run the install steps above.
