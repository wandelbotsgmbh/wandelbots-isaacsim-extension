# Wandelbots NOVA

> This extension release requires Wandelbots NOVA 25.6.

This extension lets you connect your NVIDIA Isaac Sim simulation environment with Wandelbots NOVA.

Wandelbots Nova enables easy programming of industrial robots with a brand-agnostic approach. Users can set up different robot models and teach them through a user-friendly interface or by using the provided APIs.

Currently, the Robotics Connector extension supports robot controllers from Kuka, ABB, Fanuc, Universal Robots, and Yaskawa.

The Robotics Connector is designed to simplify the connection between robots running on Wandelbots Nova and NVIDIA Omniverse (Isaac Sim). To get started with the Robotics Connector, make sure you've completed these steps:

- Create a Wandelbots Developer account (https://portal.wandelbots.io/)
- Set up a Nova instance, either in the cloud or on a physical setup. Check the documentation for more info (https://docs.wandelbots.io/)
- Learn how to set up virtual robots and connect them to NVIDIA Isaac Sim using the Wandelbots NOVA Extension (https://docs.wandelbots.io/latest/intro-simulating/)
- In the downloads section, you’ll find USD assets provided by Wandelbots to build your robotic cell environment (https://portal.wandelbots.io/en/download)


### Running the app with a secure connection (HTTPS)
To run the extension with secure connection, enable HTTPS setting by changing https enabled flag in ```config/extension.toml``` file

```
exts."omni.services.transport.server.http".https.enabled=false
```

When HTTPS is enabled, provide SSL certificate paths and other settings to establish a secure connection
```
exts."omni.services.transport.server.http".ssl.ssl_keyfile=""   # path to SSL key file
exts."omni.services.transport.server.http".ssl.ssl_certfile=""  # path to SSL certification file
exts."omni.services.transport.server.http".ssl.ssl_cert_reqs=  # optional
exts."omni.services.transport.server.http".ssl.ssl_ciphers=""  # optional
```
Once the HTTPS is enabled, Omniservice API is assessible on port 8433.


### Running the app on a custom port
By default, the extension API is available on port 8211. To configure a different port change port number in ```config/extension.toml``` file.
```
exts."omni.services.transport.server.http".port=8211 
```

