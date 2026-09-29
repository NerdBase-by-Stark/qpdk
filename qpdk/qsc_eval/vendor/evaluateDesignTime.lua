json = require("json")

-- look for and open PLuginEvalConfig.json in the current directory if it exists. Use variable names not likely to conflict with plugin code.
_EvalConfigFileName = "PluginEvalConfig.json"
_EvalConfig = nil
if io.open(_EvalConfigFileName, "r") then
  local _EvalConfigFile = io.open(_EvalConfigFileName, "r")
  local _EvalConfigContent = _EvalConfigFile:read("*all")
  _EvalConfigFile:close()
  _EvalConfig = json.decode(_EvalConfigContent)
end


Reflect = {}
Reflect.Types = {
  Unknown = "Unknown",
  Interface = "Interface",
  Display = "Display",
  Projector = "Projector",
  Amplifier = "Amplifier",
  Camera = "Camera",
  Microphone = "Microphone",
  Power = "Power",
  AudioIO = "AudioIO",
  ControlIO = "ControlIO",
  Speaker = "Speaker",
  DigitalSignage = "DigitalSignage",
  MediaPlayer = "MediaPlayer",
  MediaServer = "MediaServer",
  Network = "Network",
  VideoDistribution = "VideoDistribution",
  VideoSwitching = "VideoSwitching",
  Conferencing = "Conferencing",
  CaptureDevice = "CaptureDevice",
  Presentation = "Presentation",
  Lighting = "Lighting",
  HVAC = "HVAC",
  Shades = "Shades",
  Sensors = "Sensors",
  Calendar = "Calendar",
  Other = "Other"
}
local export = {}
export.Errors = {}
export.Warnings = {}
export.Notices = {}

local ReservedProperties = {
  "ismanaged",
  "is managed",
  "isrequired",
  "is required",
  "location",
  "showdebug",
  "show debug",
  "page_index",
  "name",
  "script access",
  "code name"
}

local ReservedControls = {
  "Status",
  "IPAddress",
  "MACAddress",
  "Username",
  "Password",
  "DeviceName",
  "DeviceFirmware",
  "SerialNumber"
}

-- Controls
local ControlKeys = {
  "Name",
  "Count",
  "ControlType",
  "PinStyle",
  "UserPin",
  "ButtonType",
  "ControlUnit",
  "IndicatorType",
  "DefaultValue",
  "Icon",
  "IconType",
  "Min",
  "Max",
  "Description"
}
local ControlTypes = {"Button", "Knob", "Indicator", "Text"}
local PinStyles = {"Input", "Output", "Both", "None"}
local ButtonTypes = {"Momentary", "Toggle", "Trigger", "StateTrigger", "On", "Off", "Custom"}
local ControlUnits = {"dB", "Hz", "Float", "Integer", "Pan", "Percent", "Position", "Seconds"}
local IndicatorTypes = {"Led", "Meter", "Text", "Status", "StatusGP"}
-- Properties
local PropTypes = {"string", "integer", "double", "boolean", "enum"}
local PropKeys = {"Name", "Type", "Header", "Value", "Min", "Max", "Choices", "Comment", "Description", "_index"}
-- Layout
local LayoutKeys = {
  "Style",
  "Position",
  "Size",
  "Color",
  "TextColor",
  "ClassName",
  "Font",
  "FontSize",
  "TextFontSize", -- alt if FontSize is not declared. FontSize = round(value*0.9)
  "FontStyle",
  "HTextAlign",
  "VTextAlign",
  "IsReadOnly",
  "Margin",
  "Padding",
  "PrettyName",
  "Radius",
  "StrokeColor",
  "StrokeWidth",
  "ZOrder",
  "IconColor"
}
local ButtonLayoutKeys = {
  "ButtonStyle",
  "ButtonVisualStyle",
  "CornerRadius",
  "CustomButtonUp",
  "CustomButtonDown",
  "Legend",
  "OffColor",
  "UnlinkOffColor",
  "WordWrap",
  "IsBold"
}
local FaderLayoutKeys = {"ShowTextbox"}
local MeterLayoutKeys = {"BackgroundColor", "MeterStyle", "ShowTextbox"}
local MeterStyles = {"Level", "Reduction", "Gain", "Standard"}
local TextLayoutKeys = {"CornerRadius", "TextBoxStyle", "WordWrap", "IsBold"}
local LedLayoutKeys = {"OffColor", "UnlinkOffColor"}
local LayoutControlStyles = {
  "Fader",
  "Knob",
  "Button",
  "Text",
  "Meter",
  "Led",
  "ListBox",
  "ComboBox",
  "Html",
  "Media",
  "None"
}

function split(inputstr, sep)
  if sep == nil then
    sep = "%s"
  end
  local t = {}
  for str in string.gmatch(inputstr, "([^" .. sep .. "]+)") do
    table.insert(t, str)
  end
  return t
end

function CopyTableItems(src, dest)
  for _, v in pairs(src) do
    table.insert(dest, v)
  end
end

local params = {...}

qplug = params[1]
while not qplug:find(".qplug") do
  if #params > 1 then
    table.remove(params, 1)
    qplug = qplug .. " " .. params[1]
  else
    error("No qplug file found")
  end
end
table.remove(params, 1)

table.indexOf = function(t, v)
  for i, _v in ipairs(t) do
    if _v == v then
      return i
    end
  end
  return nil
end

MaxProps = table.indexOf(params, "-maxprops") ~= nil

dofile(tostring(qplug))

if _EvalConfig ~= nil and _EvalConfig.Settings ~= nil then
  if _EvalConfig.Settings.MaxProps == true then
    MaxProps = true
  end
  if _EvalConfig.Settings.VarsToReset ~= nil and type(_EvalConfig.Settings.VarsToReset) == "table" then
    _EvalConfig.Storage = _EvalConfig.Storage or {}
    for _, varName in pairs(_EvalConfig.Settings.VarsToReset) do
      if _G[varName] ~= nil then
        _EvalConfig.Storage.VarsToReset = _EvalConfig.Storage.VarsToReset or {}
        _EvalConfig.Storage.VarsToReset[varName] = _G[varName]
      end
    end
  end
end

function _ResetGlobalVars()
  if _EvalConfig ~= nil and _EvalConfig.Storage ~= nil and _EvalConfig.Storage.VarsToReset ~= nil then
    for varName, varValue in pairs(_EvalConfig.Storage.VarsToReset) do
      if type(_G[varName]) == "table" then
        _G[varName] = {}
        for k, v in pairs(varValue) do
          _G[varName][k] = v
        end
      else
        _G[varName] = varValue
      end
    end
  end
end

export.PluginInfo = PluginInfo
if PluginInfo.Id == nil then
  table.insert(export.Errors, "PluginInfo.Id not set")
end
if PluginInfo.Author == nil then
  table.insert(export.Errors, "PluginInfo.Author not set")
end
if PluginInfo.Name == nil then
  table.insert(export.Errors, "PluginInfo.Name not set")
end
-- Version must be defined and in x.x, x.x.x, or x.x.x.x format
if PluginInfo.Version == nil then
  table.insert(export.Errors, "PluginInfo.Version not set")
elseif not PluginInfo.Version:match("^%d+%.%d+%.?%d*%.?%d*$") then
  table.insert(export.Errors, "PluginInfo.Version not in x.x, x.x.x, or x.x.x.x format")
end
if PluginInfo.BuildVersion ~= nil then
  table.insert(export.Notices, "PluginInfo.BuildVersion is set. This is not read by Designer and .Version should be used instead")
end
if PluginInfo.Manufacturer == nil then
  table.insert(export.Warnings, "PluginInfo.Manufacturer not set")
end


local props = {}
props.plugin_show_debug = {
  Name = "plugin_show_debug",
  Type = "boolean",
  Value = true
}

local propsTable = GetProperties(props)
_ResetGlobalVars()

for i, prop in ipairs(propsTable) do
  if prop.Name == nil then
    table.insert(export.Errors, "Property missing Name")
  else
    if prop.Type == nil then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' missing Type")
    elseif not table.indexOf(PropTypes, prop.Type) then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Type not recognized: " .. tostring(prop.Type))
    end

    if prop.Value == nil then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' missing Value")
    end

    if prop.Type == "enum" and prop.Choices == nil then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' missing Choices")
    elseif prop.Type == "enum" and prop.Choices and type(prop.Choices) ~= "table" then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Choices must be a table")
    elseif prop.Type == "enum" and prop.Choices and next(prop.Choices) == nil then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Choices must have at least one value")
    elseif prop.Type == "enum" and prop.Value and not table.indexOf(prop.Choices, prop.Value) then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Value not found in Choices: " .. tostring(prop.Value))
    end

    if prop.Type == "boolean" and prop.Value and type(prop.Value) ~= "boolean" then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Value must be a boolean")
    elseif prop.Type == "integer" and prop.Value and type(prop.Value) ~= "number" then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Value must be an integer")
    elseif prop.Type == "double" and prop.Value and type(prop.Value) ~= "number" then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Value must be a double")
    elseif prop.Type == "string" and prop.Value and type(prop.Value) ~= "string" then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Value must be a string")
    elseif prop.Type == "enum" and prop.Value and type(prop.Value) ~= "string" then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Value must be a string")
    elseif (prop.Type == "integer" or prop.Type == "double") and prop.Min and prop.Value and prop.Value < prop.Min then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Value is less than Min")
    elseif (prop.Type == "integer" or prop.Type == "double") and prop.Max and prop.Value and prop.Value > prop.Max then
      table.insert(export.Errors, "Property '" .. prop.Name .. "' Value is greater than Max")
    end

    if (prop.Type == "integer" or prop.Type == "double") then -- check for min and max
      if prop.Min == nil then
        table.insert(export.Errors, "Property '" .. prop.Name .. "' missing Min")
      elseif type(prop.Min) ~= "number" then
        table.insert(export.Errors, "Property '" .. prop.Name .. "' Min must be a number")
      end
      if prop.Max == nil then
        table.insert(export.Errors, "Property '" .. prop.Name .. "' missing Max")
      elseif type(prop.Max) ~= "number" then
        table.insert(export.Errors, "Property '" .. prop.Name .. "' Max must be a number")
      end
      if prop.Min and prop.Max and prop.Min > prop.Max then
        table.insert(export.Errors, "Property '" .. prop.Name .. "' Min is greater than Max")
      end
    end
    prop._index = i
    props[prop.Name] = prop
  end
end
props.plugin_show_debug._index = #props

if MaxProps then
  for _, prop in pairs(props) do
    if prop.Type == "integer" and prop.Max then
      prop.Value = prop.Max
    elseif prop.Type == "double" and prop.Max then
      prop.Value = prop.Max
    elseif prop.Type == "boolean" then
      prop.Value = true
    end
  end
end

-- check for prop values passed in as arguments
for _, arg in pairs(params) do
  -- prop=value or prop%20name=value
  local name, value = arg:match("([^=]+)=(.+)")
  --replace %20 with space
  if name ~= nil then
    name = name:gsub("%%20", " ")
    if name and value and props[name] then
      if props[name].Type == "string" or props[name].Type == "enum" then
        props[name].Value = tostring(value:gsub("%%20", " "))
      elseif props[name].Type == "integer" then
        props[name].Value = math.floor(tonumber(value))
      elseif props[name].Type == "double" then
        props[name].Value = tonumber(value)
      elseif props[name].Type == "boolean" then
        props[name].Value = value == "true"
      end
    end
  end
end

export.Properties = props

local controls = GetControls(props)
_ResetGlobalVars()
-- export.Controls = controls
export.Controls = {}
for _, ctrl in ipairs(controls) do
  if not ctrl.Count then
    ctrl.Count = 1
  end
  export.Controls[ctrl.Name] = ctrl
end
table.sort(export.Controls)

-- Process through all controls before layout
for _, ctrl in pairs(export.Controls) do
  -- Check for Control Keys
  for key, value in pairs(ctrl) do
    if not table.indexOf(ControlKeys, key) then
      table.insert(export.Warnings, "Control key '" .. key .. "' not recognized for control '" .. ctrl.Name .. "'")
    end
  end
end

local pluginPages = {}
if GetPages == nil then
  table.insert(export.Notices, "GetPages() function not defined. Defaulting to single page plugin.")
  pluginPages = {"main"}
else
  pluginPages = GetPages(props)
  _ResetGlobalVars()
  if #pluginPages == 0 then
    table.insert(export.Errors, "GetPages() function returned an empty table")
    pluginPages = {"main"}
  end
end
export.Pages = pluginPages

local layout = {}
local graphics = {}

for _, ctrl in pairs(export.Controls) do
  if ctrl.Count and ctrl.Count > 1 then
    for i = 1, ctrl.Count do
      layout[ctrl.Name .. " " .. i] = json.null
    end
  else
    layout[ctrl.Name] = json.null
  end
end

function isMultipleControl(ctrlName)
  -- check if control name argument ends in space then number
  if ctrlName:match(" %d+$") then
    local baseName = ctrlName:sub(1, ctrlName:find(" ") - 1)
    if export.Controls[baseName] and export.Controls[baseName].Count and export.Controls[baseName].Count > 1 then
      return true
    end
  end
  return false
end

local CodePinFound = false

for i, page in pairs(pluginPages) do
  props["page_index"] = props["page_index"] or {}
  props["page_index"].Value = i
  local _layout, _graphics = GetControlLayout(props)
  _ResetGlobalVars()
  if _layout.code and not CodePinFound then
    table.insert(export.Errors, "Code Pin detected in layout, please remove it.")
    CodePinFound = true
  end
  for layoutControlName, layoutItemTable in pairs(_layout) do
    if layout[layoutControlName] ~= nil then
      layout[layoutControlName] = layoutItemTable
      if layoutItemTable.PrettyName and export.Controls[layoutControlName] then
        export.Controls[layoutControlName].PrettyName = layoutItemTable.PrettyName
      elseif layoutItemTable.PrettyName and isMultipleControl(layoutControlName) then
        local baseName = layoutControlName:match("(.+) %d+$")
        if export.Controls[baseName] then
          export.Controls[baseName].PrettyName = layoutItemTable.PrettyName
        else
          table.insert(export.Errors, "Control '" .. baseName .. "' not found in Controls. Cannot set PrettyName")
        end
      end
    else
      table.insert(export.Errors, "Control '" .. layoutControlName .. "' not found in Controls. Cannot set layout")
    end
  end
  CopyTableItems(_graphics, graphics)
end
props["page_index"] = nil

export.Layout = layout
export.Graphics = graphics

-- Input~Input1~Select

local controlPins = {}
for _, ctrl in pairs(export.Controls) do
  if ctrl.PrettyName then
    local pinStructure = split(ctrl.PrettyName, "~")
    local path = controlPins
    local i = 1
    if #pinStructure > 1 then
      while i < #pinStructure do
        if ctrl.Count and ctrl.Count > 1 then
          -- replace number in name with [n]
          local num = pinStructure[i]:match("(%d+)")
          if num then
            pinStructure[i] = pinStructure[i]:gsub(num, "[n]")
          end
        end
        if not path[pinStructure[i]] then
          path[pinStructure[i]] = {}
        end
        path = path[pinStructure[i]]
        i = i + 1
      end
      -- path[pinStructure[i]] = ctrl
      ctrl.PinName = pinStructure[i]
      table.insert(path, ctrl)
    else
      ctrl.PinName = ctrl.PrettyName
      if controlPins[ctrl.Name] == nil then
        controlPins[ctrl.Name] = {}
      end
      controlPins[ctrl.Name] = ctrl
    end
  else
    controlPins[ctrl.Name] = ctrl
  end
end
-- table.sort(controlPins)
export.ControlPins = controlPins

-- *Tests

for _, prop in pairs(props) do
  -- Check for Property Keys
  for key, value in pairs(prop) do
    if not table.indexOf(PropKeys, key) then
      table.insert(export.Warnings, "Property key '" .. key .. "' not recognized for property '" .. prop.Name .. "'")
    end
  end
  -- Check for reserved properties
  if table.indexOf(ReservedProperties, prop.Name:lower()) then
    table.insert(export.Errors, "Property name '" .. prop.Name .. "' conflicts with reserved properties")
  end
end

-- Check Status Control Type
if not export.Controls["Status"] then
  table.insert(export.Notices, "Status Control not found")
end

-- Process through all controls
for _, ctrl in pairs(export.Controls) do
  -- Check for reserved controls
  if table.indexOf(ReservedControls, ctrl.Name) then
    table.insert(export.Notices, "Reserved Control name '" .. ctrl.Name .. "' found")
  end
  -- Check ControlTypes
  if not ctrl.ControlType then
    table.insert(export.Errors, "Control Type not set for control '" .. ctrl.Name .. "'")
  elseif not table.indexOf(ControlTypes, ctrl.ControlType) then
    table.insert(
      export.Errors,
      "Control Type '" .. ctrl.ControlType .. "' not recognized for control '" .. ctrl.Name .. "'"
    )
  end
  -- Check PinStyles
  if ctrl.PinStyle and not table.indexOf(PinStyles, ctrl.PinStyle) then
    table.insert(export.Errors, "Pin Style '" .. ctrl.PinStyle .. "' not recognized for control '" .. ctrl.Name .. "'")
  end
  -- Check ButtonTypes
  if ctrl.ControlType == "Button" then
    if ctrl.ButtonType == nil or ctrl.ButtonType == "" then
      table.insert(export.Errors, "ButtonType not set for control '" .. ctrl.Name .. "'")
    elseif not table.indexOf(ButtonTypes, ctrl.ButtonType) then
      table.insert(
        export.Errors,
        "Button Type '" .. ctrl.ButtonType .. "' not recognized for control '" .. ctrl.Name .. "'"
      )
    end
  end
  -- Check ControlUnits
  if ctrl.ControlType == "Knob" then
    if ctrl.ControlUnit == nil or ctrl.ControlUnit == "" then
      table.insert(export.Errors, "ControlUnit not set for control '" .. ctrl.Name .. "'")
    elseif not table.indexOf(ControlUnits, ctrl.ControlUnit) then
      table.insert(
        export.Errors,
        "Control Unit '" .. ctrl.ControlUnit .. "' not recognized for control '" .. ctrl.Name .. "'"
      )
    end
  end
  -- Check IndicatorTypes
  if ctrl.ControlType == "Indicator" then
    if ctrl.IndicatorType == nil or ctrl.IndicatorType == "" then
      table.insert(export.Errors, "IndicatorType not set for control '" .. ctrl.Name .. "'")
    elseif not table.indexOf(IndicatorTypes, ctrl.IndicatorType) then
      table.insert(
        export.Errors,
        "Indicator Type '" .. ctrl.IndicatorType .. "' not recognized for control '" .. ctrl.Name .. "'"
      )
    end
  end
end

-- Process Layout
for ctrlName, layoutItem in pairs(export.Layout) do
  if type(layoutItem) == "table" then
    -- Check LayoutKeys
    local AcceptedKeys = {}
    CopyTableItems(LayoutKeys, AcceptedKeys)
    if layoutItem.Style and layoutItem.Style == "Button" then
      CopyTableItems(ButtonLayoutKeys, AcceptedKeys)
    elseif layoutItem.Style and layoutItem.Style == "Fader" then
      CopyTableItems(FaderLayoutKeys, AcceptedKeys)
    elseif layoutItem.Style and layoutItem.Style == "Meter" then
      CopyTableItems(MeterLayoutKeys, AcceptedKeys)
    elseif
      layoutItem.Style and
        (layoutItem.Style == "Text" or layoutItem.Style == "ComboBox" or layoutItem.Style == "ListBox")
     then
      CopyTableItems(TextLayoutKeys, AcceptedKeys)
    elseif layoutItem.Style and layoutItem.Style == "Led" then
      CopyTableItems(LedLayoutKeys, AcceptedKeys)
    end
    for key, value in pairs(layoutItem) do
      if not table.indexOf(AcceptedKeys, key) then
        table.insert(export.Warnings, "Layout key '" .. key .. "' not recognized for '" .. ctrlName .. "'")
      end
    end
    -- Check LayoutControlStyles
    if not layoutItem.Style then
      table.insert(export.Errors, "Style not set for '" .. ctrlName .. "'")
    elseif not table.indexOf(LayoutControlStyles, layoutItem.Style) then
      table.insert(export.Errors, "Style '" .. layoutItem.Style .. "' not recognized for '" .. ctrlName .. "'")
    end
    if layoutItem.Style and layoutItem.Style == "Button" then
      -- check for ButtonStyle
      if not layoutItem.ButtonStyle then
        table.insert(export.Errors, "ButtonStyle not set for '" .. ctrlName .. "'")
      elseif not table.indexOf(ButtonTypes, layoutItem.ButtonStyle) then
        table.insert(
          export.Errors,
          "ButtonStyle '" .. layoutItem.ButtonStyle .. "' not recognized for '" .. ctrlName .. "'"
        )
      end
      -- check for ButtonVisualStyle
      if layoutItem.ButtonVisualStyle and not table.indexOf({"Flat", "Gloss"}, layoutItem.ButtonVisualStyle) then
        table.insert(
          export.Errors,
          "ButtonVisualStyle '" .. layoutItem.ButtonVisualStyle .. "' not recognized for '" .. ctrlName .. "'"
        )
      end
    end
    -- check for MeterStyle
    if layoutItem.Style and layoutItem.Style == "Meter" then
      if not layoutItem.MeterStyle then
        table.insert(export.Warnings, "MeterStyle not set for '" .. ctrlName .. "'")
      elseif not table.indexOf(MeterStyles, layoutItem.MeterStyle) then
        table.insert(
          export.Errors,
          "MeterStyle '" .. layoutItem.MeterStyle .. "' not recognized for '" .. ctrlName .. "'"
        )
      end
    end
  elseif layoutItem == json.null then
    -- Do nothing, error will be generated separately
  else
    table.insert(export.Errors, "Layout item '" .. ctrlName .. "' is not a table")
  end
end

print(json.encode(export))