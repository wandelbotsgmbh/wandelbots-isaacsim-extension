# Changelog - Wandelbots NOVA x Nvidia Isaac Sim

## 2.15.4 (2025-12-02)

### Bug Fixes

* **CSI-2233:** Include openapi spec into release

## 2.15.3 (2025-12-02)

### Bug Fixes

* Fixed missing icon and snippets

## 2.15.2 (2025-11-28)

### Bug Fixes

* **CSI-2233:** Added openapi.json to github release

## 2.15.1 (2025-11-27)

### Bug Fixes

* **CSI-2202:** extension startup errors

## 2.15.0 (2025-11-26)

### Features

* Add OmniGraph Nodes for BusIO communication e.g. ProfiNet

## 2.14.3 (2025-11-25)

### Bug Fixes

* Revert get pose change "bugfix" for ghost teaching

## 2.14.2 (2025-11-24)

### Bug Fixes

* **CSI-2217:** Fixed get xformable pose/orientation

## 2.14.1 (2025-11-18)

### Bug Fixes

* Update npm package publishing process

## 2.14.0 (2025-11-18)

### Features

* Add example on how to fetch ghost objects as variables on demand.

## 2.13.0 (2025-11-17)

### Features

* **CSI-2132:** Added move to ghost toolbar

## 2.12.1 (2025-11-17)

### Bug Fixes

* Schema extension 1.1.4 for linux support

## 2.12.0 (2025-11-10)

### Features

* Added nucleus endpoint to add an api token

## 2.11.0 (2025-11-06)

### Features

* **CSI-1728:** Wandelbots NOVA assets browser

## 2.10.6 (2025-11-06)

### Bug Fixes

* Update instances list to support multiple motion groups connected to one controller.

## 2.10.5 (2025-11-05)

### Bug Fixes

* Use torch instead of numpy for articulations

## 2.10.4 (2025-11-04)

### Bug Fixes

* Update npm publishing process.

## 2.10.3 (2025-11-04)

### Bug Fixes

* Update container registry for build images.

## 2.10.2 (2025-10-23)

### Bug Fixes

* **CSI-2142:** Fixed clear and get motion group endpoints

## 2.10.1 (2025-10-22)

### Bug Fixes

* Fixed fetch of rigid body pose

## 2.10.0 (2025-10-22)

### Features

* **CSI-2113:** Added context menu for nova tcp creation

## 2.9.0 (2025-10-22)

### Features

* **CSI-2110:** Added collision export and action planner ui (beta)

## 2.8.1 (2025-10-21)

### Bug Fixes

* Copy examples to new location.

## 2.8.0 (2025-10-20)

### Features

* Remove deprecated dependencies from omni.isaac and drop support for 4.2.0

## 2.7.3 (2025-10-14)

### Bug Fixes

* Fixed version check for minimal nova version

## 2.7.2 (2025-10-13)

### Bug Fixes

* Fixed blocking extension ref on shutdown (warning)

## 2.7.1 (2025-09-18)

### Bug Fixes

* Added python version to prebundle path

## 2.7.0 (2025-09-16)

### Features

* Added option to define width/color of individual trajectory points

## 2.6.0 (2025-09-15)

### Features

* **CIS-1948:** Merged ghost objects to single mesh. Materials are now stored next to scene

## 2.5.0 (2025-09-11)

### Features

* **RPS-1733:** Added support for NOVA OpenUSD schema

## 2.4.6 (2025-09-09)

### Bug Fixes

* Fixed missing dll on extension startup

## 2.4.5 (2025-09-06)

### Bug Fixes

* Check compatability on base_version

## 2.4.4 (2025-09-05)

### Bug Fixes

* Update order of environments for authentication

## 2.4.3 (2025-09-05)

### Bug Fixes

* Add default config if no alternative environments are given for authentication.

## 2.4.2 (2025-08-28)

### Bug Fixes

* **RPS-1990:** Fixed choosing proper portal API

## 2.4.1 (2025-08-27)

### Bug Fixes

* Added reset to ogn node config once timeline stops
* Fixed model_name namespace conflict
* Updated portal login to latest api package

## 2.4.0 (2025-08-27)

### Features

* **RPS-1922:** Compatability with 25.8.0

## 2.3.0 (2025-08-21)

### Features

* Add cloud instance workflow
* Add custom instance
* Add custom instance and cleanup
* Add folder for instances ui components
* Add icons and refeactor structure
* Add model name for robot
* Add toggle of instance status (start, stop)
* Add window which lists nova instances
* Create motion group connection between nova and isaac sim
* List motion groups
* Migrate from motion_group_name to prim_path as identifier
* Migrate to prim_path to get motion_group
* Populate instances and cells
* Refactor and update instances individually.
* Refactoring instancedata. Update UI to look bit refined.
* Remember connection state
* Separate concerns. Extract delegate logic. Restructure.
* Split up instances to distinct modules. Update wandelbots-nova to 2.9.0
* Update connect and disconnect functionality
* Update credential store to persist auth token
* Update icons.py to use os instead of omni.kit.app
* Update motion group configuration
* Update openapi spec and update deletion of instance
* Update signup process and cleanup
* Update spaces and colors and show error message when motion group is already in use.

### Bug Fixes

* Add message when no instance is available
* Check if we pull for auth token
* Delete and styling and code formatting
* Instance form cancel button was not working
* Make custom instances work again
* Refresh after delete
* Remove conflicting dependencies
* Remove custom instance was not working.
* Removing motion group to prim path connection was not possible
* Show message when no cloud instances are available
* Support multiple cells again
* Update authentication endpoint compatible
* Update rendering
* Update UI and cleanup colors.

## 2.2.2 (2025-08-21)

### Bug Fixes

* Update naming of python client

## 2.2.1 (2025-08-20)

### Bug Fixes

* Updated api client publishing

## 2.2.0 (2025-08-20)

### Features

* Added truth table and range select node generation scripts

## 2.1.2 (2025-07-29)

### Bug Fixes

* **RPS-1916:** Moved required pip packages to extension

## 2.1.1 (2025-07-28)

### Bug Fixes

* **RPS-1913:** Updated control mode switch to new endpoint in NOVA 25.7.0

## 2.1.0 (2025-07-28)

### Features

* **RPS-1356:** Collision export

## 2.0.2 (2025-07-16)

### Bug Fixes

* **RPS-1869:** Upgraded connector models and enabled auto control mode switch for ext. joint stream

## 2.0.1 (2025-07-13)

### Bug Fixes

* Changed nova sdk version to 1.9.1

## 2.0.0 (2025-07-09)

### Features

* Made endpoints RESTful
* Optimized doc strings
* Added query and body request parameters
* Handled deprecated types in data types
* Refactored prims utils
* Renamed robot to motion-group
* Simplified camera endpoints

## 1.48.1 (2025-06-30)

### Bug Fixes

* Fixed empty IO subscription artifacts after unsubscribe
* Fixed invalid IOs not being filtered out correctly
* Fixed reconnect triggering reset every time

## 1.48.0 (2025-06-26)

### Features

* **RPS-1819:** Compatability release for Wandelbots NOVA 25.6

## 1.47.7 (2025-05-06)

### Bug Fixes

* upload to azure storage account

## 1.47.6 (2025-04-16)

### Bug Fixes

* Fixed crash in extension shutdown

## 1.47.5 (2025-04-16)

### Bug Fixes

* inverse first and inverse second modes when complex rots are present
* relative pose orientations

## 1.47.4 (2025-04-15)

### Bug Fixes

* Fixed used base_url endpoint for authentication

## 1.47.3 (2025-04-09)

### Bug Fixes

* **RPS-1527:** Fixed invalid async in ghost object creation

## 1.47.2 (2025-03-27)

### Bug Fixes

* missing __init__.py file for loading extension

## 1.47.1 (2025-03-20)

### Bug Fixes

* point cloud output datatype

## 1.47.0 (2025-03-20)

### Features

* Added OGN IO nodes (read/write/onchange)

## 1.46.0 (2025-03-20)

### Features

* add base template for configurable camera
* add camera parameters models
* add datatypes for configurable camera
* add dependeny injection for fetching configurable camera
* add endpoints to delete cameras
* add fetching and deleting configured camera endpoints
* capture bounding box data in structured format
* capture segmentation data in structured format
* change endpoints for camera router

### Bug Fixes

* bounding box data formats for issac sim 4.5
* capturing depth, normals data
* capturing pc data
* change query paramters to body for labels
* code to capture segemntaiton data
* endpoints to capture segmentation data
* endpoints to capture synethetic data
* fetching cam params and setting cam params
* fetching camera paramters in structured format
* formatting errors
* formatting issues
* formatting issues for static analysis
* get active camera validation error
* package imports for synthetic data
* semantic segmentation datatypes
* setting camera parameters for 4.5
* virtual camera configuration model
* visualization of 2d bounding boxes

### Performance Improvements

* optimise reading and writing camera paramters for Isaac sim 4.5

## 1.45.2 (2025-03-17)

### Bug Fixes

* Lazy loading of nova.auth dependency

## 1.45.1 (2025-03-14)

### Bug Fixes

* Allow physical robots to be connected
* Check for joints in motion group state.

## 1.45.0 (2025-03-13)

### Features

* Add X-Wandelbots-Client to API requests.

## 1.44.1 (2025-03-12)

### Bug Fixes

* Authentication inside of Isaac Sim was not possible

## 1.44.0 (2025-03-07)

### Features

* Remove singleton for authentication
* Update authorization process.
* Use default values if .env is not available

### Bug Fixes

* Add missing auth token to calls

## 1.43.5 (2025-02-20)

### Bug Fixes

* Fixed initial timeline start not connecting robot state stream

## 1.43.4 (2025-02-17)

### Bug Fixes

* Capture synthetic data without the requirement of viewport switch

## 1.43.3 (2025-02-14)

### Bug Fixes

* Toggle visibility, joints and collider of prims

## 1.43.2 (2025-02-13)

### Bug Fixes

* Fixed path of postprocessing script

## 1.43.1 (2025-02-12)

### Bug Fixes

* Update synthetic data capturing and error handling

## 1.43.0 (2025-02-12)

### Features

* Change module from 'com.wandelbots.omniservice' to 'wandelbots.omni'
* Read dependencies from extension.toml.

## 1.42.1 (2025-02-10)

### Bug Fixes

* **RPS-1204:** Robot stream connects when timeline play is executed

## 1.42.0 (2025-02-10)

### Features

* **RPS-285:** Added option to synchronize simulation articulation state

### Bug Fixes

* Update wandelbots-nova dependency

## 1.41.0 (2025-02-05)

### Features

* Add device code auth0 process
* Update authentication endpoint to store the token directly
* Update authorization flow

### Bug Fixes

* Check for token when connecting to websocket.

## 1.40.2 (2025-02-03)

### Bug Fixes

* Added websocket reconnect
* Made WS connection error more noticeable

## 1.40.1 (2025-01-31)

### Bug Fixes

* Isaac Sim 4.5 compatability
* Utilize settings to get the current port of the API

## 1.40.0 (2025-01-31)

### Features

* add https config
* add port config to launch omniservice
* add script to generate self-signed certificates
* reload app settings to apply https settings

### Reverts

* custom port to default

## 1.39.1 (2025-01-29)

### Bug Fixes

* Replaced ext id by name

## 1.39.0 (2025-01-29)

### Features

* Extract object modifiers into PrimUtils

### Bug Fixes

* Fixes the issue that get_pose and get_relative_pose was not responding anymore.

## 1.38.4 (2025-01-28)

### Bug Fixes

* Fixed OmniService extension not found
* Fixed unused import error

## 1.38.3 (2025-01-28)

### Bug Fixes

* Fixed out of bounds error in get tcp sources
* tcp source prims are not filtered by prefix

## 1.38.2 (2025-01-27)

### Bug Fixes

* Add python package to module mapping in configuration to resolve versioning issues.

## 1.38.1 (2025-01-27)

### Bug Fixes

* Remove 'scene' from exported config to prevent issues when reimporting it

## 1.38.0 (2025-01-23)

### Features

* Introduce "Wandelbots" menubar item

## 1.37.3 (2025-01-22)

### Bug Fixes

* ghost material path

## 1.37.2 (2025-01-21)

### Bug Fixes

* Remove conveyor.ui dependency

## 1.37.1 (2025-01-21)

### Bug Fixes

* Remove  as dependency.

## 1.37.0 (2025-01-21)

### Features

* Add cleanup to CHANGELOG.md
* Create Github Release with Release Notes
* Exchange preview image.
* Rename connector and add changelogs and readme
* Set preview image.

## 1.36.2 (2025-01-20)

### Bug Fixes

* await expressions
* Update release process.

## 1.36.1 (2025-01-14)

### Bug Fixes

* Remove `await` from get_pose occurences to fix camera endpoints

## 1.36.0 (2024-12-20)

### Features

* Changed hide ui endpoint and added getter

## 1.35.0 (2024-12-16)

### Features

* Add version endpoint to get versions of installed extensions. Remove login endpoint.

## 1.34.0 (2024-12-09)

### Features

* add endpoint to fetch all TCP sources
* add multiple modes for capturing relative pose between prims
* fetch tcp sources with values
* save ghost sources and objects in a different scope object

### Bug Fixes

* fetch ghost object sources only when created in the scene

### Performance Improvements

* optimise fetching ghost object sources

## 1.33.1 (2024-11-21)

### Bug Fixes

* Upload extension to azure storage

## 1.33.0 (2024-11-15)

### Features

* Add endpoint to select an object based on its prim path
* add get_ghost_object_sources endpoint
* add get_ghost_objects endpoint
* add GhostRubyRed material USD file
* add new ghost material
* Add select ghost_object method to the ghost teaching router
* add set relative pose endpoint
* add tcp transformation to prim
* add the ability to create more than one ghost object
* Added delete and create (with name) ghost object
* apply specified material
* create main ghost during robot creation
* create source ghost objects with tcp names
* create source ghosts when creating robots
* enable ghost object to be placed at ref pose
* get robot prim path for ghost object
* hide source ghost prim
* implement websocket endpoint
* implment copy_ghost_material_to_scene
* mark ghost objects via prim data
* obtian the pose
* remove physics apis and prims
* Set position of ghost to the tcp_flange of the robot
* set ref_pose to move ghost object
* Update openapi.json
* use the cloner extension to clone objects

### Bug Fixes

* Add poses folder for ghost objects
* adjust a doc string
* applying shader to prim
* Awaiting selection of object
* cloner error with quatf and quatd types
* Copy ghost object material at the correct place
* delete source key on copy
* delete the ghost object after exiting the generator
* ghost material physics
* handling source ghosts
* improve error handling on copy_ghost_material_to_scene
* omit ghost objects as main sources
* openapi version
* Readd select ghost object, add object at flange pose
* ref_pose parameter when creating ghost object
* serialization of json objects
* set pose endpoint for handing doubles and floats
* simplify the apply material command
* Update relative pose calculation
* use generators to enable more efficient traversal of the scene.
* visibility of created ghost objects
* Wait 2 seconds before sending updates of the ghost objects in scene to increase performance

### Performance Improvements

* change pose tracker wait time to 3 seconds
* optimise getting ghost object data from scene

## 1.32.0 (2024-11-11)

### Features

* Add authenticate endpoint for oauth next to login for backwards compatibility
* Enable omniservice to authenticate with OAuth Token
* Provide basic or bearer token to endpoint

## 1.31.0 (2024-11-08)

### Features

* Update response rate of robot state stream to 32 as default and fix parameter

## 1.30.0 (2024-11-07)

### Features

* Add endpoints to toggle joints
* Update OpenAPI

## 1.29.0 (2024-10-26)

### Features

* add endpoints to toggle collider

## 1.28.0 (2024-10-18)

### Features

* add endpoints to control prim visibility

## 1.27.2 (2024-09-10)

### Bug Fixes

* pin the azure cli image and adjust script to new package manager
* remove an unnecessary before_script stage

## 1.27.1 (2024-09-09)

### Bug Fixes

* delete connected streams and tools when deleting a robot
* don't mutate the array while iterating over it
* only stop a single stream, not any connected ones
* use the correct key
* use the delete_robot function to delete all robots
* use the stream name for deletion

## 1.27.0 (2024-08-20)

### Features

* Compatability with Isaac Sim 4.1
* Compatability with Isaac Sim 4.1
* Ignore velocities for robot for now.
* Ignore velocities for robot for now.

### Reverts

* Revert changes in postprocessing
* Revert changes in postprocessing

## 1.26.0 (2024-08-20)

### Features

* Upload releases to the portal again

## 1.25.0 (2024-08-12)

### Features

* Change also type annotation

### Bug Fixes

* Add internal development packages to wandelbots-internal for referencing.
* Apply postprocessing for union types in npm package
* Update optional signals field to non optional. Update configuration interfaces to more convenient ones.

### Reverts

* Revert changes in postprocessing

## 1.24.2 (2024-07-26)

### Bug Fixes

* increase clarity of a docstring
* mutation of host database when exporting config
* transfer changes from the workstation
* use single_dispatch to simplify tree-walk

### Reverts

* return scene file during export

## 1.24.1 (2024-07-17)

### Bug Fixes

* Better error handling for unknown tool metadata.

## 1.24.0 (2024-07-03)

### Features

* add analog gripper
* add custom tool data schemas
* add endpoint to determine analog signals based on positions
* add gripper ranges model
* add joint velocities for articulation chains
* configure tool metadata info
* enable analog gripper states with multiple IOs
* enable multi joint drive with digital signals

### Bug Fixes

* analog articulation chain
* check for signals in states before creating tool
* check state change before tool action
* endpoint for fetching analog signals based on joint positions
* exporting configuration for gripper
* IO streams for gripper
* loading keys in configuration
* signals when starting io stream
* surface gripper method
* validation conditions for tools

### Performance Improvements

* check for empty websocket messages

## 1.23.1 (2024-06-21)

### Bug Fixes

* relative pose

## 1.23.0 (2024-06-19)

### Features

* add clear all variables function  in inmemory datbase
* find tool paths from registered data
* register and deregister prim data

### Bug Fixes

* property for base tool class
* reset host database when scene is changed

## 1.22.0 (2024-06-18)

### Features

* delete associated tool streams when parent robot stream is deleted

### Bug Fixes

* check start_all_streams and delete_all_streams endpoints
* start stream only when not running
* stop stream when deleted
* streamer condition in stop stream

## 1.21.2 (2024-06-14)

### Bug Fixes

* Update regex for version inside of openapi.json

## 1.21.1 (2024-06-14)

### Bug Fixes

* Update list of assets to change the version entry.

## 1.21.0 (2024-06-14)

### Features

* add authentication to http requests
* add authentication to websockets
* add credential store

### Bug Fixes

* check for unsecured connections for login
* loading configuration file with missing keys
* make username and password optional
* missing robot keys
* reading keys in streams
* robot headers
* sending authorization bearer token
* websocket connections with authentication

### Reverts

* delimiter in database

## 1.20.1 (2024-06-12)

### Bug Fixes

* Update files for version bump

## 1.20.0 (2024-06-12)

### Features

* add better documentation to the "set_state" endpoint
* improve docstrings
* move setting of the tool state to an abstract method _apply_tool_state
* provide better errors for the apply_action endpoint

### Bug Fixes

* reinitialize tools on the start of the IO stream

## 1.19.3 (2024-06-11)

### Bug Fixes

* disable https by default

## 1.19.2 (2024-06-11)

### Bug Fixes

* is_Secured key
* load scenes in config file with missing keys

## 1.19.1 (2024-06-11)

### Bug Fixes

* Lock thread when receiving io streaming messages to avoid race conditions.

## 1.19.0 (2024-06-11)

### Features

* add secured network protocols

## 1.18.1 (2024-06-07)

### Bug Fixes

* articulation for generic parallel gripper
* endpoint names
* gripper modes reachability
* logging for all tools
* mode variable
* stop streams when simulation is not running
* streaming variable
* Use different version for semantic release dependencies.

## 1.18.0 (2024-05-31)


### Features

* Update README.md which is shown in the library of nvidia

## 1.17.0 (2024-05-30)


### Features

* control streams individually
* endpoints to control streams
* streammanager to control streams


### Bug Fixes

* fetching all streams
* return types for streams

## 1.16.0 (2024-05-27)


### Features

* add key types while loading config
* control move direction for conveyor


### Bug Fixes

* conveyor event prim
* conveyor velocity check and gripper params
* revert back to old conveyor setup
* revert GenericSurfaceGripper back to generalized convention
* revert to old suction gripper
* velocity param for conveyor

## 1.15.0 (2024-05-22)


### Features

* auto fetch tools
* fetch all robot prim paths in the scene

## 1.14.0 (2024-05-22)


### Features

* Add api middleware for CORS and update OpenAPI spec
* Add reponse models and opaque configuration names in openapi spec
* Each virtual robot state connector subscribes to the physics step event
* Implement streaming endpoint for VirtualRobot
* Parse VirtualRobot state stream data and push it into the omniverse articulation
* Remove virtual robot state connector
* Update required data for robot and stream


### Bug Fixes

* io dict keys
* revert type annotations
* Update response parsing of IO stream

## 1.13.0 (2024-05-22)


### Features

* Upload versioned artifact to developer portal

## 1.12.0 (2024-05-21)


### Features

* auto generation of openapi spec file on startup


### Bug Fixes

* change status request to GET
* check for existing tools ans streams
* remove check for existing streams

## 1.11.2 (2024-05-17)


### Bug Fixes

* **VW:** Fix VW Gripper

## 1.11.1 (2024-05-10)


### Bug Fixes

* Return Status Code 200 for the simulation is running endpoint instead of 204

## 1.11.0 (2024-05-08)


### Features

* add simulation controls (play, pause and stop)
* load and save scene configurations


### Bug Fixes

* extension reload issue
* generic parallel gripper
* issues with model definitions when reloading app
* load and save configuration
* operation ids for streams
* prevent duplicate creation of tools, robots and streams
* streaming route


### Performance Improvements

* redirect api reference to stoplight UI
* redirect api reference to stoplight UI

## 1.10.0 (2024-04-29)


### Features

* add poses feature


### Bug Fixes

* aesthetics
* disable host validation and add warning for wrong connection
* loading ws files
* ws API path execution and delete scripts


### Performance Improvements

* get path by selecting object

## 1.9.0 (2024-04-11)


### Features

* add a sample configuration file
* add api route
* add config validator
* add configurable tool
* add endpoint for tool operations
* add endpoint to apply action for configured tools
* add get and delete stream endpoints
* add io state streaming endpoints
* add IO streaming
* add pose tracker streaming for omniverse
* add robot state streaming
* add robot state streaming endpoints
* add schamalz_sction_gripper
* add schunk gripper
* add status for streams
* add streams registry
* add stremaing connector base class
* add tool endpoints with configurable tool
* add ToolIO datatype
* associate tools with robots
* check for duplicates in stream configuration
* check network connections before streaming
* configure robots from .yaml file
* configure tools from .yaml file
* connect tool control with IO streaming
* connect tools to a robot instance
* connect tools to a robot instance
* connect tools to a robot instance
* control tools via state
* create configurable streaming services
* load IOs based on robots input for streaming
* load scene from configuration
* modified pose tracking using streaming
* parse robot state with robot_state_streaming
* refactor rae connector
* refactor virtual robot
* refine tool endpoints
* reorganized structure
* show viewport to full screen


### Bug Fixes

* add httpx dependency
* add reuse option for validator
* articulation for schunk gripper
* async call for set camera parameters
* generic surface gripper
* IO streaming and tool connections
* loading parameters from dict
* loading streaming parameters from dict
* multi-robot state streaming
* parallel gripper articulation
* robot state streaming endpoints
* run tasks in background
* setting pose of object
* stream fetcher


### Performance Improvements

* simplified exception handling

## 1.8.3 (2024-03-26)


### Bug Fixes

* Missing options did not allow for attaching tools

## 1.8.2 (2024-03-15)


### Bug Fixes

* async call in reset prim

## 1.8.1 (2024-2-27)


### Bug Fixes

* minor fixes in point cloud and bounding box captures
* semantic labels

## 1.8.0 (2024-2-26)


### Features

* add nested inmemory database
* add reset endpoints
* add reset prim option based on default defined poses
* add setting pose for different prim types
* add support for all prim types
* endpoints are simplified for poses
* reset objects poses for all children prims

## 1.7.0 (2024-2-26)


### Features

* add auto-installation of external python packages on application startup
* add datatypes
* add datatypes for coordinate system and rotations
* add docstrings for all functions and endpoints
* add typing hints and variable types
* disable stage units to get pose in WS format irrespective of units set
* remove generic sensor


### Bug Fixes

* add response models for validation
* async call for fetching prim
* typos
* websocket route poses


### Reverts

* bring back support for poses extension
* ubuntu to 20.04 and cuda to 11.4
