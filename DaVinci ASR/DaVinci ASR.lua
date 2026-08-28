math.randomseed(os.time())

local App = {}

-- 1. Config
do
    local Config = {}
    local separator = package.config:sub(1, 1)

    local function scriptDirectory()
        local source = debug.getinfo(1, "S").source or ""
        if source:sub(1, 1) == "@" then
            source = source:sub(2)
        end
        local pattern = separator == "\\" and "(.*[\\/])" or "(.*[/])"
        local value = source:match(pattern) or "."
        return value:gsub("[\\/]$", "")
    end

    local function join(base, name)
        if base:sub(-1) == separator then
            return base .. name
        end
        return base .. separator .. name
    end

    local function parent(path)
        return path:match("^(.*)[/\\][^/\\]+$") or path
    end

    local function fileExists(path)
        local stream = io.open(path, "rb")
        if stream then
            stream:close()
            return true
        end
        return false
    end

    Config.SCRIPT_NAME = "DaVinci ASR"
    Config.SCRIPT_VERSION = "1.0.0"
    Config.MORE_FEATURES_URL = "https://www.heibagen.com/plugins"
    Config.SUPABASE_URL = "https://ctojqwfhfctnwyffcsvc.supabase.co"
    Config.SUPABASE_PUBLISHABLE_KEY = "sb_publishable_1aGEOf370Geh2P0sUTSCQg_Vys3FxWm"
    Config.UPDATE_TIMEOUT = 5
    Config.ERROR_LOG_THROTTLE_SECONDS = 30
    Config.WINDOW_ID = "DaVinciASRWindow"
    Config.PROTOCOL_VERSION = 1
    Config.SEPARATOR = separator
    Config.IS_WINDOWS = separator == "\\"
    Config.SCRIPT_DIR = scriptDirectory()
    Config.PROJECT_ROOT = parent(Config.SCRIPT_DIR)
    Config.INSTALL_CONFIG_DIR = join(Config.SCRIPT_DIR, "config")
    Config.INSTALL_SETTINGS_FILE = join(Config.INSTALL_CONFIG_DIR, "setting.json")
    Config.RENDER_PRESET_NAME = "render_to_asr_wav"
    Config.RENDER_PRESET_FILE = join(join(Config.SCRIPT_DIR, "render_preset"), "render_to_asr_wav.xml")
    Config.AUDIO_TEMP_DIR = join(Config.SCRIPT_DIR, "audio_temp")
    Config.MODELS_DIR = join(Config.SCRIPT_DIR, "models")

    local root
    if Config.IS_WINDOWS then
        root = join(join(os.getenv("LOCALAPPDATA") or "", "HEIBA"), "DaVinciASR")
    else
        root = join(join(join(os.getenv("HOME") or "", "Library"), "Application Support"), "HEIBA")
        root = join(root, "DaVinciASR")
    end
    Config.APP_ROOT = root
    Config.CONFIG_DIR = join(root, "settings")
    Config.CACHE_DIR = join(root, "cache")
    Config.SETTINGS_FILE = join(Config.CONFIG_DIR, "setting.json")
    Config.TEMP_DIR = join(root, "temp")
    Config.IPC_DIR = join(root, "ipc")
    Config.RUNTIME_STATUS_FILE = join(root, "runtime_status.json")
    Config.RUNTIME_LOCK_FILE = join(root, "runtime.lock")
    Config.RUNTIME_LAUNCH_PID_FILE = join(root, "runtime_launch.pid")
    Config.RUNTIME_OWNER_FILE = join(root, "runtime_owner.flag")
    Config.UPDATE_RESULT_FILE = join(Config.CACHE_DIR, "update_result.json")
    Config.UPDATE_DONE_FILE = join(Config.CACHE_DIR, "update_done.flag")
    local productionRuntime
    local legacyProductionRuntime
    if Config.IS_WINDOWS then
        Config.RUNTIME_DIR = join(root, "runtime")
        productionRuntime = join(Config.RUNTIME_DIR, "DaVinci ASR.exe")
        legacyProductionRuntime = join(Config.RUNTIME_DIR, "DaVinciASRRuntime.exe")
    else
        Config.RUNTIME_DIR = "/Library/Application Support/HEIBA/DaVinciASR/runtime"
        productionRuntime = join(
            join(join(join(Config.RUNTIME_DIR, "DaVinci ASR.app"), "Contents"), "MacOS"),
            "DaVinci ASR"
        )
        legacyProductionRuntime = join(
            join(join(join(Config.RUNTIME_DIR, "DaVinci ASR Runtime.app"), "Contents"), "MacOS"),
            "DaVinciASRRuntime"
        )
    end
    Config.PRODUCTION_RUNTIME_EXECUTABLE = productionRuntime
    Config.LEGACY_PRODUCTION_RUNTIME_EXECUTABLE = legacyProductionRuntime
    Config.DEV_RUNTIME_EXECUTABLE = join(join(Config.PROJECT_ROOT, ".dev-runtime"), "DaVinci ASR")
    Config.LEGACY_DEV_RUNTIME_EXECUTABLE = join(join(Config.PROJECT_ROOT, ".dev-runtime"), "DaVinciASRRuntime")
    Config.RUNTIME_EXECUTABLE = productionRuntime
    Config.RUNTIME_KIND = "production"
    local runtimeOverride = os.getenv("DAVINCI_ASR_RUNTIME")
    if runtimeOverride and runtimeOverride ~= "" then
        Config.RUNTIME_EXECUTABLE = runtimeOverride
        Config.RUNTIME_KIND = "override"
    elseif fileExists(Config.DEV_RUNTIME_EXECUTABLE) then
        Config.RUNTIME_EXECUTABLE = Config.DEV_RUNTIME_EXECUTABLE
        Config.RUNTIME_KIND = "development"
    elseif fileExists(Config.LEGACY_DEV_RUNTIME_EXECUTABLE) then
        Config.RUNTIME_EXECUTABLE = Config.LEGACY_DEV_RUNTIME_EXECUTABLE
        Config.RUNTIME_KIND = "development"
    elseif fileExists(productionRuntime) then
        Config.RUNTIME_EXECUTABLE = productionRuntime
    elseif fileExists(legacyProductionRuntime) then
        Config.RUNTIME_EXECUTABLE = legacyProductionRuntime
        Config.RUNTIME_KIND = "legacy-production"
    end

    Config.LANGUAGES = {
        "Auto", "Chinese", "English", "Cantonese", "French", "German",
        "Italian", "Japanese", "Korean", "Portuguese", "Russian", "Spanish"
    }
    Config.LANGUAGE_LABELS = {
        cn = {
            "自动检测（推荐）", "中文（普通话）", "英语", "粤语", "法语", "德语",
            "意大利语", "日语", "韩语", "葡萄牙语", "俄语", "西班牙语"
        },
        en = {
            "Auto Detect (Recommended)", "Chinese (Mandarin)", "English", "Cantonese",
            "French", "German", "Italian", "Japanese", "Korean", "Portuguese",
            "Russian", "Spanish"
        }
    }
    Config.ALLOWED_STATES = {
        queued = true,
        preparing = true,
        loading_asr = true,
        transcribing = true,
        unloading_asr = true,
        loading_aligner = true,
        aligning = true,
        segmenting = true,
        writing_srt = true,
        done = true,
        cancelled = true,
        error = true
    }
    Config.DEFAULTS = {
        language = "Auto",
        prompt = "",
        max_chars = 42,
        remove_gaps = false,
        trim_end_punctuation = false,
        ui_language = "en"
    }
    App.Config = Config
end

-- 2. Core
do
    local Core = {}
    Core.resolve = resolve
    if not Core.resolve and type(Resolve) == "function" then
        Core.resolve = Resolve()
    end
    Core.fusion = Core.resolve and Core.resolve:Fusion() or fusion
    Core.ui = Core.fusion and Core.fusion.UIManager or nil
    Core.dispatcher = Core.ui and bmd and bmd.UIDispatcher(Core.ui) or nil
    Core.job = nil
    Core.runtimeStatus = nil
    Core.timer = nil
    App.Core = Core
end

-- 3. Utils
do
    local Utils = {}
    local Config = App.Config
    local JSON_NULL = {}
    local errorLogTimes = {}

    function Utils.joinPath(base, name)
        if base == "" then
            return name
        end
        if base:sub(-1) == Config.SEPARATOR then
            return base .. name
        end
        return base .. Config.SEPARATOR .. name
    end

    function Utils.fileExists(path)
        local stream = io.open(path, "rb")
        if stream then
            stream:close()
            return true
        end
        return false
    end

    function Utils.shellQuote(value)
        value = tostring(value or "")
        if Config.IS_WINDOWS then
            return '"' .. value:gsub('"', '""') .. '"'
        end
        return "'" .. value:gsub("'", "'\\''") .. "'"
    end

    function Utils.powerShellQuote(value)
        return "'" .. tostring(value or ""):gsub("'", "''") .. "'"
    end

    function Utils.commandSucceeded(first, second, third)
        if first == true or first == 0 then
            return true
        end
        return second == "exit" and third == 0
    end

    function Utils.logError(code, detail)
        local safeCode = tostring(code or "UNKNOWN_ERROR"):upper():gsub("[^A-Z0-9_%-]", "_")
        local safeDetail = tostring(detail or "unknown error")
            :gsub("[\r\n\t]+", " ")
            :gsub("%s+", " ")
            :gsub("Bearer%s+[%w%-%._~%+/=]+", "Bearer [redacted]")
            :gsub("sb_[%w_%-]+", "sb_[redacted]")
            :gsub("hf_[%w_%-]+", "hf_[redacted]")
            :gsub("sk%-[%w_%-]+", "sk-[redacted]")
        if #safeDetail > 500 then
            safeDetail = safeDetail:sub(1, 497) .. "..."
        end
        local signature = safeCode .. "\0" .. safeDetail
        local now = os.time()
        local previous = errorLogTimes[signature]
        if previous and now - previous < Config.ERROR_LOG_THROTTLE_SECONDS then
            return false
        end
        errorLogTimes[signature] = now
        print(string.format("[DaVinci ASR][ERROR][%s] %s", safeCode, safeDetail))
        return true
    end

    function Utils.trim(value)
        return tostring(value or ""):match("^%s*(.-)%s*$") or ""
    end

    function Utils.versionIsNewer(candidate, current)
        local function parse(value)
            local normalized = Utils.trim(value)
            if normalized:sub(1, 1):lower() == "v" then
                normalized = normalized:sub(2)
            end
            normalized = normalized:match("^([^%+%-]+)") or normalized
            local major, minor, patch = normalized:match("^(%d+)%.(%d+)%.(%d+)$")
            if not major then
                return nil
            end
            return { tonumber(major), tonumber(minor), tonumber(patch) }
        end

        local left = parse(candidate)
        local right = parse(current)
        if not left or not right then
            return false
        end
        for index = 1, 3 do
            if left[index] ~= right[index] then
                return left[index] > right[index]
            end
        end
        return false
    end

    function Utils.urlEncode(value)
        return tostring(value or ""):gsub("([^%w%-_%.~])", function(char)
            return string.format("%%%02X", string.byte(char))
        end)
    end

    function Utils.ensureDir(path)
        if not path or path == "" then
            return false
        end
        local command
        if Config.IS_WINDOWS then
            command = "cmd.exe /D /C if not exist " .. Utils.shellQuote(path) .. " mkdir " .. Utils.shellQuote(path)
        else
            command = "/bin/mkdir -p " .. Utils.shellQuote(path)
        end
        local first, second, third = os.execute(command)
        return Utils.commandSucceeded(first, second, third)
    end

    function Utils.parentDirectory(path)
        local escaped = Config.SEPARATOR == "\\" and "[\\/]" or "/"
        return path:match("^(.*" .. escaped .. ")")
    end

    function Utils.basename(path)
        return tostring(path or ""):match("([^/\\]+)$") or tostring(path or "")
    end

    function Utils.removeTree(path, expectedLeaf)
        if not path or path == "" or Utils.basename(path) ~= expectedLeaf then
            return false, "Refusing to remove an unexpected directory."
        end
        local command
        if Config.IS_WINDOWS then
            command = "cmd.exe /D /C if exist " .. Utils.shellQuote(path)
                .. " rmdir /S /Q " .. Utils.shellQuote(path)
        else
            command = "/bin/rm -rf -- " .. Utils.shellQuote(path)
        end
        local result = os.execute(command)
        return result == true or result == 0
    end

    function Utils.cleanupTemporaryData()
        local targets = {
            { path = Config.AUDIO_TEMP_DIR, leaf = "audio_temp" },
            { path = Config.TEMP_DIR, leaf = "temp" },
            { path = Config.IPC_DIR, leaf = "ipc" }
        }
        local failures = {}
        for _, target in ipairs(targets) do
            local removed, removeError = Utils.removeTree(target.path, target.leaf)
            if not removed then
                failures[#failures + 1] = target.path .. ": " .. tostring(removeError or "remove failed")
            end
        end
        if #failures > 0 then
            return false, table.concat(failures, "; ")
        end
        return true
    end

    local function jsonEscape(value)
        local replacements = {
            ['"'] = '\\"',
            ['\\'] = '\\\\',
            ['\b'] = '\\b',
            ['\f'] = '\\f',
            ['\n'] = '\\n',
            ['\r'] = '\\r',
            ['\t'] = '\\t'
        }
        return value:gsub('[%z\1-\31\\"]', function(char)
            return replacements[char] or string.format("\\u%04x", char:byte())
        end)
    end

    local function isArray(value)
        local count = 0
        local maximum = 0
        for key, _ in pairs(value) do
            if type(key) ~= "number" or key < 1 or key ~= math.floor(key) then
                return false, 0
            end
            count = count + 1
            if key > maximum then
                maximum = key
            end
        end
        return count > 0 and count == maximum, maximum
    end

    local function encodeJson(value, stack)
        local kind = type(value)
        if value == JSON_NULL or kind == "nil" then
            return "null"
        elseif kind == "boolean" then
            return value and "true" or "false"
        elseif kind == "number" then
            if value ~= value or value == math.huge or value == -math.huge then
                error("Cannot encode a non-finite JSON number")
            end
            return tostring(value)
        elseif kind == "string" then
            return '"' .. jsonEscape(value) .. '"'
        elseif kind ~= "table" then
            error("Unsupported JSON type: " .. kind)
        end
        if stack[value] then
            error("Cannot encode a recursive table")
        end
        stack[value] = true
        local array, maximum = isArray(value)
        local parts = {}
        if array then
            for index = 1, maximum do
                parts[#parts + 1] = encodeJson(value[index], stack)
            end
            stack[value] = nil
            return "[" .. table.concat(parts, ",") .. "]"
        end
        for key, item in pairs(value) do
            if type(key) ~= "string" then
                error("JSON object keys must be strings")
            end
            parts[#parts + 1] = encodeJson(key, stack) .. ":" .. encodeJson(item, stack)
        end
        table.sort(parts)
        stack[value] = nil
        return "{" .. table.concat(parts, ",") .. "}"
    end

    function Utils.jsonEncode(value)
        return encodeJson(value, {})
    end

    local function utf8Character(codepoint)
        if codepoint <= 0x7F then
            return string.char(codepoint)
        elseif codepoint <= 0x7FF then
            return string.char(0xC0 + math.floor(codepoint / 0x40), 0x80 + (codepoint % 0x40))
        elseif codepoint <= 0xFFFF then
            return string.char(
                0xE0 + math.floor(codepoint / 0x1000),
                0x80 + (math.floor(codepoint / 0x40) % 0x40),
                0x80 + (codepoint % 0x40)
            )
        end
        return string.char(
            0xF0 + math.floor(codepoint / 0x40000),
            0x80 + (math.floor(codepoint / 0x1000) % 0x40),
            0x80 + (math.floor(codepoint / 0x40) % 0x40),
            0x80 + (codepoint % 0x40)
        )
    end

    local function decodeJson(text)
        local position = 1
        local length = #text

        local function skipWhitespace()
            while position <= length and text:sub(position, position):match("%s") do
                position = position + 1
            end
        end

        local parseValue

        local function parseString()
            position = position + 1
            local parts = {}
            while position <= length do
                local char = text:sub(position, position)
                if char == '"' then
                    position = position + 1
                    return table.concat(parts)
                elseif char == "\\" then
                    position = position + 1
                    local escape = text:sub(position, position)
                    local simple = {
                        ['"'] = '"', ['\\'] = '\\', ['/'] = '/',
                        b = '\b', f = '\f', n = '\n', r = '\r', t = '\t'
                    }
                    if simple[escape] then
                        parts[#parts + 1] = simple[escape]
                        position = position + 1
                    elseif escape == "u" then
                        local hex = text:sub(position + 1, position + 4)
                        local codepoint = tonumber(hex, 16)
                        if not codepoint then
                            error("Invalid JSON unicode escape")
                        end
                        position = position + 5
                        if codepoint >= 0xD800 and codepoint <= 0xDBFF and text:sub(position, position + 1) == "\\u" then
                            local low = tonumber(text:sub(position + 2, position + 5), 16)
                            if low and low >= 0xDC00 and low <= 0xDFFF then
                                codepoint = 0x10000 + (codepoint - 0xD800) * 0x400 + (low - 0xDC00)
                                position = position + 6
                            end
                        end
                        parts[#parts + 1] = utf8Character(codepoint)
                    else
                        error("Invalid JSON escape")
                    end
                else
                    parts[#parts + 1] = char
                    position = position + 1
                end
            end
            error("Unterminated JSON string")
        end

        local function parseArray()
            position = position + 1
            local result = {}
            skipWhitespace()
            if text:sub(position, position) == "]" then
                position = position + 1
                return result
            end
            while true do
                result[#result + 1] = parseValue()
                skipWhitespace()
                local char = text:sub(position, position)
                if char == "]" then
                    position = position + 1
                    return result
                elseif char ~= "," then
                    error("Expected ',' or ']' in JSON array")
                end
                position = position + 1
                skipWhitespace()
            end
        end

        local function parseObject()
            position = position + 1
            local result = {}
            skipWhitespace()
            if text:sub(position, position) == "}" then
                position = position + 1
                return result
            end
            while true do
                if text:sub(position, position) ~= '"' then
                    error("Expected JSON object key")
                end
                local key = parseString()
                skipWhitespace()
                if text:sub(position, position) ~= ":" then
                    error("Expected ':' after JSON object key")
                end
                position = position + 1
                skipWhitespace()
                result[key] = parseValue()
                skipWhitespace()
                local char = text:sub(position, position)
                if char == "}" then
                    position = position + 1
                    return result
                elseif char ~= "," then
                    error("Expected ',' or '}' in JSON object")
                end
                position = position + 1
                skipWhitespace()
            end
        end

        function parseValue()
            skipWhitespace()
            local char = text:sub(position, position)
            if char == '"' then
                return parseString()
            elseif char == "{" then
                return parseObject()
            elseif char == "[" then
                return parseArray()
            elseif text:sub(position, position + 3) == "true" then
                position = position + 4
                return true
            elseif text:sub(position, position + 4) == "false" then
                position = position + 5
                return false
            elseif text:sub(position, position + 3) == "null" then
                position = position + 4
                return JSON_NULL
            end
            local numberText = text:sub(position):match("^%-?%d+%.?%d*[eE]?[+%-]?%d*")
            if numberText and numberText ~= "" then
                position = position + #numberText
                return tonumber(numberText)
            end
            error("Invalid JSON value at byte " .. tostring(position))
        end

        local value = parseValue()
        skipWhitespace()
        if position <= length then
            error("Trailing data after JSON value")
        end
        return value
    end

    function Utils.jsonDecode(text)
        return decodeJson(text)
    end

    function Utils.httpGet(url, headers, timeout)
        local executable = Config.IS_WINDOWS and "curl.exe" or "/usr/bin/curl"
        local parts = {
            Utils.shellQuote(executable),
            "-fsS",
            "--noproxy", Utils.shellQuote("*"),
            "--max-time", tostring(math.max(1, math.floor(tonumber(timeout) or Config.UPDATE_TIMEOUT)))
        }
        for key, value in pairs(headers or {}) do
            parts[#parts + 1] = "-H"
            parts[#parts + 1] = Utils.shellQuote(tostring(key) .. ": " .. tostring(value))
        end
        parts[#parts + 1] = Utils.shellQuote(url)
        local command = table.concat(parts, " ") .. (Config.IS_WINDOWS and " 2>NUL" or " 2>/dev/null")
        local opened, pipe = pcall(io.popen, command, "r")
        if not opened or not pipe then
            return nil, "curl_popen_failed"
        end
        local body = pipe:read("*a") or ""
        local first, second, third = pipe:close()
        if not Utils.commandSucceeded(first, second, third) then
            return nil, "request_failed"
        end
        if body == "" then
            return nil, "empty_response"
        end
        return body
    end

    function Utils.fetchUpdateManifest(pluginId)
        local url = Config.SUPABASE_URL .. "/functions/v1/check_update_v2?pid="
            .. Utils.urlEncode(pluginId)
        local body, requestError = Utils.httpGet(url, {
            apikey = Config.SUPABASE_PUBLISHABLE_KEY,
            ["Content-Type"] = "application/json",
            ["User-Agent"] = Config.SCRIPT_NAME .. "/" .. Config.SCRIPT_VERSION
        }, Config.UPDATE_TIMEOUT)
        if not body then
            return nil, requestError
        end
        local ok, value = pcall(Utils.jsonDecode, body)
        if not ok or type(value) ~= "table" then
            return nil, "invalid_response"
        end
        return value
    end

    function Utils.startUpdateManifestFetch(pluginId)
        if not Utils.ensureDir(Config.CACHE_DIR) then
            return false, "cache_directory_unavailable"
        end
        os.remove(Config.UPDATE_RESULT_FILE)
        os.remove(Config.UPDATE_DONE_FILE)
        local temporary = Config.UPDATE_RESULT_FILE .. ".tmp." .. tostring(os.time())
            .. "." .. tostring(math.random(100000, 999999))
        os.remove(temporary)
        local url = Config.SUPABASE_URL .. "/functions/v1/check_update_v2?pid="
            .. Utils.urlEncode(pluginId)
        local command
        if Config.IS_WINDOWS then
            local inner = table.concat({
                "$ErrorActionPreference = 'SilentlyContinue';",
                "& curl.exe -fsS --noproxy '*' --max-time " .. tostring(Config.UPDATE_TIMEOUT),
                "-H " .. Utils.powerShellQuote("apikey: " .. Config.SUPABASE_PUBLISHABLE_KEY),
                "-H " .. Utils.powerShellQuote("Content-Type: application/json"),
                "-H " .. Utils.powerShellQuote("User-Agent: " .. Config.SCRIPT_NAME .. "/" .. Config.SCRIPT_VERSION),
                "--output " .. Utils.powerShellQuote(temporary),
                Utils.powerShellQuote(url) .. ";",
                "if ($LASTEXITCODE -eq 0) { Move-Item -LiteralPath "
                    .. Utils.powerShellQuote(temporary) .. " -Destination "
                    .. Utils.powerShellQuote(Config.UPDATE_RESULT_FILE) .. " -Force };",
                "Set-Content -LiteralPath " .. Utils.powerShellQuote(Config.UPDATE_DONE_FILE)
                    .. " -Value 'done' -Encoding Ascii"
            }, " ")
            local outer = "Start-Process -FilePath 'powershell.exe' "
                .. "-ArgumentList @("
                .. "'-NoLogo','-NoProfile','-NonInteractive','-Command',"
                .. Utils.powerShellQuote(inner) .. ") -WindowStyle Hidden"
            command = "powershell.exe -NoLogo -NoProfile -NonInteractive -Command "
                .. Utils.shellQuote(outer)
        else
            local curl = table.concat({
                "/usr/bin/curl", "-fsS", "--noproxy", Utils.shellQuote("*"),
                "--max-time", tostring(Config.UPDATE_TIMEOUT),
                "-H", Utils.shellQuote("apikey: " .. Config.SUPABASE_PUBLISHABLE_KEY),
                "-H", Utils.shellQuote("Content-Type: application/json"),
                "-H", Utils.shellQuote("User-Agent: " .. Config.SCRIPT_NAME .. "/" .. Config.SCRIPT_VERSION),
                "--output", Utils.shellQuote(temporary), Utils.shellQuote(url)
            }, " ")
            local script = "umask 077; ( if " .. curl .. "; then /bin/mv -f "
                .. Utils.shellQuote(temporary) .. " " .. Utils.shellQuote(Config.UPDATE_RESULT_FILE)
                .. "; else /bin/rm -f " .. Utils.shellQuote(temporary) .. "; fi; /bin/echo done > "
                .. Utils.shellQuote(Config.UPDATE_DONE_FILE) .. " ) >/dev/null 2>&1 &"
            command = "/bin/sh -c " .. Utils.shellQuote(script)
        end
        local first, second, third = os.execute(command)
        if not Utils.commandSucceeded(first, second, third) then
            os.remove(temporary)
            return false, "background_update_launch_failed"
        end
        return true
    end

    function Utils.openExternalUrl(url)
        if url ~= Config.MORE_FEATURES_URL then
            return false, "unexpected_url"
        end
        local command
        if Config.IS_WINDOWS then
            command = "cmd.exe /D /C start \"\" " .. Utils.shellQuote(url)
        else
            command = "/usr/bin/open " .. Utils.shellQuote(url)
        end
        local first, second, third = os.execute(command)
        if Utils.commandSucceeded(first, second, third) then
            return true
        end
        if not Config.IS_WINDOWS then
            first, second, third = os.execute("xdg-open " .. Utils.shellQuote(url))
            if Utils.commandSucceeded(first, second, third) then
                return true
            end
        end
        return false, "open_failed"
    end

    function Utils.readText(path)
        local stream, errorMessage = io.open(path, "rb")
        if not stream then
            return nil, errorMessage
        end
        local value = stream:read("*a")
        stream:close()
        return value
    end

    function Utils.readJson(path)
        local text, readError = Utils.readText(path)
        if not text then
            return nil, readError
        end
        local ok, value = pcall(Utils.jsonDecode, text)
        if not ok then
            return nil, value
        end
        return value
    end

    function Utils.atomicWriteText(path, text)
        local parent = Utils.parentDirectory(path)
        if parent then
            Utils.ensureDir(parent)
        end
        local temporary = path .. ".tmp." .. tostring(os.time()) .. "." .. tostring(math.random(100000, 999999))
        local stream, openError = io.open(temporary, "wb")
        if not stream then
            return false, openError
        end
        local ok, writeError = stream:write(text)
        if ok then
            stream:flush()
        end
        stream:close()
        if not ok then
            os.remove(temporary)
            return false, writeError
        end
        local renamed, renameError = os.rename(temporary, path)
        if not renamed then
            -- IPC request/cancel destinations are write-once. This fallback is
            -- used only for replacing the external user settings file.
            os.remove(path)
            renamed, renameError = os.rename(temporary, path)
        end
        if not renamed then
            os.remove(temporary)
            return false, renameError
        end
        return true
    end

    function Utils.atomicWriteJson(path, value)
        return Utils.atomicWriteText(path, Utils.jsonEncode(value) .. "\n")
    end

    function Utils.generateJobId(prefix)
        return (prefix or "job") .. "-" .. os.date("!%Y%m%dT%H%M%S") .. "-" .. tostring(math.random(100000, 999999))
    end

    function Utils.launchRuntime()
        if not Utils.fileExists(Config.RUNTIME_EXECUTABLE) then
            return false, "Private Runtime is not installed. Install DaVinci ASR first."
        end
        local currentStatus = Utils.runtimeStatus()
        if not Utils.runtimeHeartbeatFresh(currentStatus) then
            local stalePids = Utils.runtimePidCandidates()
            if #stalePids > 0 then
                local stopped, stopError = Utils.stopRuntime()
                if not stopped then
                    return false, stopError
                end
            end
        end
        local ownerWritten, ownerError = Utils.atomicWriteText(
            Config.RUNTIME_OWNER_FILE,
            Utils.generateJobId("owner") .. "\n"
        )
        if not ownerWritten then
            return false, "Could not create the Runtime lifetime file: " .. tostring(ownerError)
        end
        os.remove(Config.RUNTIME_LAUNCH_PID_FILE)
        local command
        if Config.IS_WINDOWS then
            local arguments = table.concat({
                Utils.powerShellQuote("--models-root"),
                Utils.powerShellQuote('"' .. Config.MODELS_DIR .. '"'),
                Utils.powerShellQuote("--owner-file"),
                Utils.powerShellQuote('"' .. Config.RUNTIME_OWNER_FILE .. '"'),
                Utils.powerShellQuote("daemon")
            }, ", ")
            local script = "$ErrorActionPreference = 'Stop'; $env:PYTHONHOME = $null; $env:PYTHONPATH = $null; "
                .. "$process = Start-Process -FilePath "
                .. Utils.powerShellQuote(Config.RUNTIME_EXECUTABLE)
                .. " -ArgumentList @(" .. arguments .. ") -WindowStyle Hidden -PassThru; "
                .. "Set-Content -LiteralPath " .. Utils.powerShellQuote(Config.RUNTIME_LAUNCH_PID_FILE)
                .. " -Value $process.Id -Encoding Ascii"
            command = "powershell.exe -NoLogo -NoProfile -NonInteractive -Command "
                .. Utils.shellQuote(script)
        else
            local launch = "/usr/bin/env -u PYTHONHOME -u PYTHONPATH "
                .. Utils.shellQuote(Config.RUNTIME_EXECUTABLE)
                .. " --models-root " .. Utils.shellQuote(Config.MODELS_DIR)
                .. " --owner-file " .. Utils.shellQuote(Config.RUNTIME_OWNER_FILE)
                .. " daemon >/dev/null 2>&1 & child=$!; umask 077; /bin/echo \"$child\" > "
                .. Utils.shellQuote(Config.RUNTIME_LAUNCH_PID_FILE)
            command = "/bin/sh -c " .. Utils.shellQuote(launch)
        end
        local first, second, third = os.execute(command)
        if not Utils.commandSucceeded(first, second, third) then
            os.remove(Config.RUNTIME_OWNER_FILE)
            return false, "Could not launch the private Runtime."
        end
        return true
    end

    function Utils.runtimePidCandidates()
        local result = {}
        local seen = {}
        local function add(value)
            local pid = tostring(value or ""):match("^%s*(%d+)%s*$")
            pid = tonumber(pid)
            if pid and pid >= 2 and pid == math.floor(pid) and not seen[pid] then
                seen[pid] = true
                result[#result + 1] = pid
            end
        end
        local status = Utils.runtimeStatus()
        if type(status) == "table" then
            add(status.pid)
        end
        local lockText = Utils.readText(Config.RUNTIME_LOCK_FILE)
        if lockText then
            add(lockText:match("^%s*(%d+)"))
        end
        local launchText = Utils.readText(Config.RUNTIME_LAUNCH_PID_FILE)
        if launchText then
            add(launchText)
        end
        return result
    end

    function Utils.runtimeProcessCommand(pid)
        local command
        if Config.IS_WINDOWS then
            command = 'cmd.exe /D /C tasklist /FI "PID eq ' .. tostring(pid) .. '" /FO CSV /NH'
        else
            command = "/bin/ps -p " .. tostring(pid) .. " -o command="
        end
        local opened, pipe = pcall(io.popen, command, "r")
        if not opened or not pipe then
            return ""
        end
        local value = pipe:read("*a") or ""
        pipe:close()
        return value
    end

    function Utils.runtimeProcessMatches(pid)
        if Config.IS_WINDOWS then
            local script = "$process = Get-Process -Id " .. tostring(pid)
                .. " -ErrorAction SilentlyContinue; if ($process -and $process.ProcessName -in "
                .. "@('DaVinci ASR', 'DaVinciASRRuntime')) { exit 0 }; exit 1"
            local first, second, third = os.execute(
                "powershell.exe -NoLogo -NoProfile -NonInteractive -Command "
                .. Utils.shellQuote(script)
            )
            return Utils.commandSucceeded(first, second, third)
        end
        local command = Utils.runtimeProcessCommand(pid):lower()
        if command == "" then
            return false
        end
        local isDaemon = command:find("daemon", 1, true) ~= nil
        local isRuntime = command:find("davinci asr", 1, true) ~= nil
            or command:find("davinci%-asr/runtime/main.py") ~= nil
            or command:find("davinciasrruntime", 1, true) ~= nil
        return isDaemon and isRuntime
    end

    function Utils.runtimeProcessAlive(pid)
        if Config.IS_WINDOWS then
            local script = "if (Get-Process -Id " .. tostring(pid)
                .. " -ErrorAction SilentlyContinue) { exit 0 }; exit 1"
            local first, second, third = os.execute(
                "powershell.exe -NoLogo -NoProfile -NonInteractive -Command "
                .. Utils.shellQuote(script)
            )
            return Utils.commandSucceeded(first, second, third)
        end
        local first, second, third = os.execute(
            "/bin/kill -0 " .. tostring(pid) .. " >/dev/null 2>&1"
        )
        return Utils.commandSucceeded(first, second, third)
    end

    function Utils.terminateRuntimeProcess(pid)
        local command
        if Config.IS_WINDOWS then
            command = "cmd.exe /D /C taskkill /PID " .. tostring(pid) .. " /T /F >NUL 2>&1"
        else
            command = "/bin/sh -c " .. Utils.shellQuote(
                "/usr/bin/pkill -TERM -P " .. tostring(pid) .. " 2>/dev/null || true; "
                .. "/bin/kill -TERM " .. tostring(pid) .. " 2>/dev/null || true; "
                .. "count=0; while /bin/kill -0 " .. tostring(pid)
                .. " 2>/dev/null && [ \"$count\" -lt 20 ]; do /bin/sleep 0.1; count=$((count + 1)); done; "
                .. "if /bin/kill -0 " .. tostring(pid) .. " 2>/dev/null; then "
                .. "/usr/bin/pkill -KILL -P " .. tostring(pid) .. " 2>/dev/null || true; "
                .. "/bin/kill -KILL " .. tostring(pid) .. " 2>/dev/null || true; fi; "
                .. "count=0; while /bin/kill -0 " .. tostring(pid)
                .. " 2>/dev/null && [ \"$count\" -lt 20 ]; do /bin/sleep 0.1; count=$((count + 1)); done; "
                .. "! /bin/kill -0 " .. tostring(pid) .. " 2>/dev/null"
            )
        end
        os.execute(command)
        return not Utils.runtimeProcessAlive(pid)
    end

    function Utils.stopRuntime()
        os.remove(Config.RUNTIME_OWNER_FILE)
        local pids = Utils.runtimePidCandidates()
        local failures = {}
        for _, pid in ipairs(pids) do
            if Utils.runtimeProcessAlive(pid) then
                if not Utils.runtimeProcessMatches(pid) then
                    failures[#failures + 1] = tostring(pid) .. " (identity mismatch)"
                elseif not Utils.terminateRuntimeProcess(pid) then
                    failures[#failures + 1] = tostring(pid) .. " (still running)"
                end
            end
        end
        if #failures > 0 then
            return false, "Runtime process could not be stopped: " .. table.concat(failures, ", ")
        end
        os.remove(Config.RUNTIME_LAUNCH_PID_FILE)
        os.remove(Config.RUNTIME_LOCK_FILE)
        os.remove(Config.RUNTIME_STATUS_FILE)
        return true
    end

    function Utils.runtimeStatus()
        return Utils.readJson(Config.RUNTIME_STATUS_FILE)
    end

    function Utils.runtimeHeartbeatFresh(status)
        if type(status) ~= "table" then
            return false
        end
        if status.running == false then
            return false
        end
        local heartbeat = tonumber(status.heartbeat or 0) or 0
        return os.time() - heartbeat <= 10
    end

    function Utils.formatSrtTimestamp(seconds)
        local milliseconds = math.max(0, math.floor((tonumber(seconds) or 0) * 1000 + 0.5))
        local hours = math.floor(milliseconds / 3600000)
        milliseconds = milliseconds % 3600000
        local minutes = math.floor(milliseconds / 60000)
        milliseconds = milliseconds % 60000
        local secs = math.floor(milliseconds / 1000)
        local millis = milliseconds % 1000
        return string.format("%02d:%02d:%02d,%03d", hours, minutes, secs, millis)
    end

    App.Utils = Utils
end

-- 4. Settings
do
    local Settings = {}
    local Config = App.Config
    local Utils = App.Utils

    local function copyDefaults()
        local result = {}
        for key, value in pairs(Config.DEFAULTS) do
            result[key] = value
        end
        return result
    end

    function Settings:load()
        local values = copyDefaults()
        local stored, loadError = Utils.readJson(Config.SETTINGS_FILE)
        if type(stored) == "table" then
            for key, defaultValue in pairs(Config.DEFAULTS) do
                if type(stored[key]) == type(defaultValue) then
                    values[key] = stored[key]
                end
            end
        elseif Utils.fileExists(Config.SETTINGS_FILE) then
            Utils.logError("SETTINGS_LOAD_FAILED", loadError)
        end
        return values
    end

    function Settings:save(values)
        local source = values or Config.DEFAULTS
        local payload = {
            language = tostring(source.language or "Auto"),
            prompt = tostring(source.prompt or ""),
            max_chars = tonumber(source.max_chars) or 42,
            remove_gaps = source.remove_gaps == true,
            trim_end_punctuation = source.trim_end_punctuation == true,
            ui_language = source.ui_language == "en" and "en" or "cn"
        }
        if not Utils.ensureDir(Config.CONFIG_DIR) then
            Utils.logError("SETTINGS_DIR_FAILED", "Could not create the settings directory.")
            return false, "Could not create the settings directory."
        end
        local saved, saveError = Utils.atomicWriteJson(Config.SETTINGS_FILE, payload)
        if not saved then
            Utils.logError("SETTINGS_SAVE_FAILED", saveError)
        end
        return saved, saveError
    end

    App.Settings = Settings
end

-- 5. Resolve bridge
do
    local R = {}
    local Core = App.Core
    local Config = App.Config
    local Utils = App.Utils
    R.renderState = nil
    R.audioCachePaths = {}

    function R:returnToEditPage()
        if not Core.resolve then
            return false, "DaVinci Resolve API is unavailable."
        end
        local ok, result = pcall(function()
            return Core.resolve:OpenPage("edit")
        end)
        if not ok then
            Utils.logError("OPEN_EDIT_PAGE_FAILED", result)
            return false, tostring(result)
        end
        if result ~= true then
            Utils.logError("OPEN_EDIT_PAGE_REJECTED", "Resolve returned " .. tostring(result))
            return false, "Resolve could not open the Edit page."
        end
        return true
    end

    function R:getContext()
        if not Core.resolve then
            return nil, "DaVinci Resolve API is unavailable."
        end
        local manager = Core.resolve:GetProjectManager()
        local project = manager and manager:GetCurrentProject() or nil
        local timeline = project and project:GetCurrentTimeline() or nil
        local mediaPool = project and project:GetMediaPool() or nil
        if not project then
            return nil, "Open a Resolve project first."
        end
        if not timeline then
            return nil, "Open a timeline first."
        end
        return { project = project, timeline = timeline, mediaPool = mediaPool }
    end

    function R:getFrameRate(context)
        local raw = context.timeline:GetSetting("timelineFrameRate")
        if not raw or tostring(raw) == "" then
            raw = context.project:GetSetting("timelineFrameRate")
        end
        local text = tostring(raw or ""):upper()
        local drop = text:find("DF", 1, true) ~= nil
        text = text:gsub("%s*DF", ""):gsub("%s+", "")
        local mapping = {
            ["23.976"] = { numerator = 24000, denominator = 1001, protocol = "24000/1001", nominal = 24 },
            ["29.97"] = { numerator = 30000, denominator = 1001, protocol = "30000/1001", nominal = 30 },
            ["59.94"] = { numerator = 60000, denominator = 1001, protocol = "60000/1001", nominal = 60 },
            ["24"] = { numerator = 24, denominator = 1, protocol = "24", nominal = 24 },
            ["25"] = { numerator = 25, denominator = 1, protocol = "25", nominal = 25 },
            ["30"] = { numerator = 30, denominator = 1, protocol = "30", nominal = 30 },
            ["50"] = { numerator = 50, denominator = 1, protocol = "50", nominal = 50 },
            ["60"] = { numerator = 60, denominator = 1, protocol = "60", nominal = 60 }
        }
        local rate = mapping[text]
        if not rate then
            return nil, "Unsupported timeline frame rate: " .. tostring(raw)
        end
        rate.drop = drop and (rate.nominal == 30 or rate.nominal == 60)
        return rate
    end

    local function framesToTimecode(frameNumber, rate)
        local frames = math.max(0, math.floor(frameNumber + 0.5))
        local separator = ":"
        if rate.drop then
            local dropFrames = rate.nominal == 60 and 4 or 2
            local framesPer10Minutes = rate.nominal * 60 * 10 - dropFrames * 9
            local framesPerMinute = rate.nominal * 60 - dropFrames
            local framesPer24Hours = (rate.nominal * 60 * 60 - dropFrames * 54) * 24
            frames = frames % framesPer24Hours
            local tenMinuteBlocks = math.floor(frames / framesPer10Minutes)
            local remainder = frames % framesPer10Minutes
            local dropped = dropFrames * 9 * tenMinuteBlocks
            if remainder >= dropFrames then
                dropped = dropped + dropFrames * math.floor((remainder - dropFrames) / framesPerMinute)
            end
            frames = frames + dropped
            separator = ";"
        end
        local hours = math.floor(frames / (rate.nominal * 3600))
        frames = frames % (rate.nominal * 3600)
        local minutes = math.floor(frames / (rate.nominal * 60))
        frames = frames % (rate.nominal * 60)
        local seconds = math.floor(frames / rate.nominal)
        local frame = frames % rate.nominal
        return string.format("%02d:%02d:%02d%s%02d", hours, minutes, seconds, separator, frame)
    end

    function R:getTimecodeContext(startFrame)
        local context = self:getContext()
        if not context then
            return nil
        end
        local rate = self:getFrameRate(context)
        if not rate then
            return nil
        end
        return {
            rate = rate,
            startFrame = tonumber(startFrame)
                or tonumber(context.timeline:GetStartFrame() or 0)
                or 0
        }
    end

    function R:secondsToTimecode(seconds, timecodeContext)
        local value = timecodeContext or self:getTimecodeContext()
        if not value or not value.rate then
            return "00:00:00:00"
        end
        local rate = value.rate
        local relativeFrames = math.floor((tonumber(seconds) or 0) * rate.numerator / rate.denominator + 0.5)
        return framesToTimecode((tonumber(value.startFrame) or 0) + relativeFrames, rate)
    end

    function R:jumpToSeconds(seconds, startFrame)
        local context, contextError = self:getContext()
        if not context then
            return false, contextError
        end
        local pageOk, currentPage = pcall(function()
            return Core.resolve:GetCurrentPage()
        end)
        if pageOk and currentPage ~= "cut" and currentPage ~= "edit"
            and currentPage ~= "color" and currentPage ~= "fairlight"
            and currentPage ~= "deliver" then
            self:returnToEditPage()
        end
        local rate, rateError = self:getFrameRate(context)
        if not rate then
            return false, rateError
        end
        local relativeFrames = math.floor((tonumber(seconds) or 0) * rate.numerator / rate.denominator + 0.5)
        local baseFrame = tonumber(startFrame)
            or tonumber(context.timeline:GetStartFrame() or 0)
            or 0
        return context.timeline:SetCurrentTimecode(framesToTimecode(baseFrame + relativeFrames, rate)) == true
    end

    function R:getTimelineRenderRange(context)
        if not context or not context.timeline then
            return nil, "Timeline context is unavailable."
        end
        local timelineStartFrame = tonumber(context.timeline:GetStartFrame())
        local timelineEndFrame = tonumber(context.timeline:GetEndFrame())
        if not timelineStartFrame or not timelineEndFrame or timelineEndFrame < timelineStartFrame then
            return nil, "Resolve returned an invalid timeline frame range."
        end

        local marksCallOk, marks = pcall(function()
            return context.timeline:GetMarkInOut()
        end)
        if not marksCallOk then
            return nil, "Resolve could not read the timeline In/Out points: " .. tostring(marks)
        end
        if type(marks) ~= "table" then
            marks = {}
        end

        local hasPartialMark = false
        for _, markType in ipairs({ "audio", "video", "all" }) do
            local range = marks[markType]
            if type(range) == "table" then
                local markIn = tonumber(range["in"])
                local markOut = tonumber(range["out"])
                if markIn ~= nil or markOut ~= nil then
                    hasPartialMark = true
                end
                if markIn ~= nil and markOut ~= nil then
                    if markOut < markIn then
                        return nil, "Timeline Out point must be after the In point."
                    end
                    local subtitleStartFrame = markIn
                    if timelineStartFrame > 0 and markIn < timelineStartFrame then
                        subtitleStartFrame = timelineStartFrame + markIn
                    end
                    return {
                        selectAllFrames = false,
                        markType = markType,
                        markIn = markIn,
                        markOut = markOut,
                        timelineStartFrame = timelineStartFrame,
                        timelineEndFrame = timelineEndFrame,
                        subtitleStartFrame = subtitleStartFrame
                    }
                end
            end
        end
        if hasPartialMark then
            return nil, "Set both timeline In and Out points, or clear both points."
        end
        return {
            selectAllFrames = true,
            markType = "none",
            timelineStartFrame = timelineStartFrame,
            timelineEndFrame = timelineEndFrame,
            subtitleStartFrame = timelineStartFrame
        }
    end

    function R:getAudioCacheState(jobId)
        local context, contextError = self:getContext()
        if not context then
            return nil, contextError
        end
        local rate, rateError = self:getFrameRate(context)
        if not rate then
            return nil, rateError
        end
        local renderRange, rangeError = self:getTimelineRenderRange(context)
        if not renderRange then
            return nil, rangeError
        end
        local timelineName = tostring(context.timeline:GetName() or "Timeline")
        local safeName = timelineName:gsub("[^%w%-]+", "_"):sub(1, 64)
        local safeJobId = tostring(jobId or Utils.generateJobId("asr")):gsub("[^%w%-]", "")
        local audioFilePrefix = safeName .. "_" .. safeJobId .. "_audio_temp"
        local audioPath = Utils.joinPath(Config.AUDIO_TEMP_DIR, audioFilePrefix .. ".wav")
        R.audioCachePaths[audioPath] = true
        return {
            context = context,
            timelineName = timelineName,
            jobId = safeJobId,
            audioFilePrefix = audioFilePrefix,
            audioPath = audioPath,
            rate = rate,
            startFrame = renderRange.subtitleStartFrame,
            subtitleStartFrame = renderRange.subtitleStartFrame,
            timelineStartFrame = renderRange.timelineStartFrame,
            timelineEndFrame = renderRange.timelineEndFrame,
            selectAllFrames = renderRange.selectAllFrames,
            markType = renderRange.markType,
            markIn = renderRange.markIn,
            markOut = renderRange.markOut
        }
    end

    function R:existingAudioPath(cacheState)
        if cacheState and Utils.fileExists(cacheState.audioPath) then
            return cacheState.audioPath
        end
        return nil
    end

    function R:cleanupAudioCache()
        Utils.removeTree(Config.AUDIO_TEMP_DIR, "audio_temp")
        R.audioCachePaths = {}
    end

    function R:discardAudioCache(cacheState)
        if not cacheState or not cacheState.audioPath then
            return
        end
        os.remove(cacheState.audioPath)
    end

    function R:startAudioRender(cacheState)
        if not cacheState or not cacheState.context then
            return nil, "Timeline audio cache state is unavailable."
        end
        local context = cacheState.context
        if context.project:IsRenderingInProgress() then
            return nil, "Resolve is already rendering. Wait for the current render to finish."
        end
        if not Utils.fileExists(Config.RENDER_PRESET_FILE) then
            return nil, "Bundled render_to_asr_wav.xml is missing."
        end
        local targetDirectory = Config.AUDIO_TEMP_DIR
        Utils.ensureDir(targetDirectory)
        local previousFormat = context.project:GetCurrentRenderFormatAndCodec()
        local previousMode = context.project:GetCurrentRenderMode()
        local function restorePreviousSelection()
            if previousFormat and previousFormat.format and previousFormat.codec then
                context.project:SetCurrentRenderFormatAndCodec(previousFormat.format, previousFormat.codec)
            end
            if previousMode ~= nil then
                context.project:SetCurrentRenderMode(previousMode)
            end
        end
        Core.resolve:ImportRenderPreset(Config.RENDER_PRESET_FILE)
        if context.project:LoadRenderPreset(Config.RENDER_PRESET_NAME) ~= true then
            restorePreviousSelection()
            self:returnToEditPage()
            return nil, "Resolve could not load the bundled render_to_asr_wav preset."
        end
        local settings = {
            SelectAllFrames = cacheState.selectAllFrames ~= false,
            ExportVideo = false,
            ExportAudio = true,
            TargetDir = targetDirectory,
            CustomName = cacheState.audioFilePrefix
        }
        if cacheState.selectAllFrames == false then
            settings.MarkIn = cacheState.markIn
            settings.MarkOut = cacheState.markOut
        end
        local settingsCallOk, settingsResult = pcall(function()
            return context.project:SetRenderSettings(settings)
        end)
        if not settingsCallOk then
            restorePreviousSelection()
            self:returnToEditPage()
            return nil, "Resolve raised an error while applying the bundled WAV render settings: "
                .. tostring(settingsResult)
        end
        if settingsResult ~= true then
            if cacheState.selectAllFrames == false then
                restorePreviousSelection()
                self:returnToEditPage()
                return nil, "Resolve rejected the timeline In/Out render range."
            end
            Utils.logError(
                "RENDER_SETTINGS_FALLBACK",
                "SetRenderSettings returned " .. tostring(settingsResult)
                    .. "; continuing with the loaded WAV preset."
            )
        end
        local renderJobId = context.project:AddRenderJob()
        if not renderJobId then
            restorePreviousSelection()
            self:returnToEditPage()
            return nil, "Resolve could not create an audio render job."
        end
        if context.project:StartRendering({ renderJobId }, false) ~= true then
            context.project:DeleteRenderJob(renderJobId)
            restorePreviousSelection()
            self:returnToEditPage()
            return nil, "Resolve could not start the audio render job."
        end
        R.renderState = {
            context = context,
            renderJobId = renderJobId,
            previousFormat = previousFormat,
            previousMode = previousMode,
            audioPath = cacheState.audioPath,
            audioFilePrefix = cacheState.audioFilePrefix,
            rate = cacheState.rate,
            startFrame = cacheState.startFrame,
            subtitleStartFrame = cacheState.subtitleStartFrame or cacheState.startFrame,
            timelineStartFrame = cacheState.timelineStartFrame,
            timelineEndFrame = cacheState.timelineEndFrame,
            selectAllFrames = cacheState.selectAllFrames,
            markType = cacheState.markType,
            markIn = cacheState.markIn,
            markOut = cacheState.markOut
        }
        return R.renderState
    end

    function R:restoreRenderState()
        local state = R.renderState
        if not state then
            self:returnToEditPage()
            return
        end
        pcall(function()
            state.context.project:DeleteRenderJob(state.renderJobId)
        end)
        if state.previousFormat and state.previousFormat.format and state.previousFormat.codec then
            pcall(function()
                state.context.project:SetCurrentRenderFormatAndCodec(
                    state.previousFormat.format,
                    state.previousFormat.codec
                )
            end)
        end
        if state.previousMode ~= nil then
            pcall(function()
                state.context.project:SetCurrentRenderMode(state.previousMode)
            end)
        end
        R.renderState = nil
        self:returnToEditPage()
    end

    function R:pollAudioRender()
        local state = R.renderState
        if not state then
            return nil, 0, nil, "No render is active."
        end
        local info = state.context.project:GetRenderJobStatus(state.renderJobId) or {}
        local progress = tonumber(
            info.CompletionPercentage or info["Completion Percentage"] or info.completionPercentage or 0
        ) or 0
        local jobState = tostring(info.JobStatus or info.Status or info.status or "")
        if jobState:lower():find("fail", 1, true) then
            self:restoreRenderState()
            return true, progress, nil, "Timeline audio render failed."
        end
        if state.context.project:IsRenderingInProgress() then
            return false, progress, nil, nil
        end
        local audioPath = state.audioPath
        self:restoreRenderState()
        if not Utils.fileExists(audioPath) then
            return true, 100, nil, "Resolve completed rendering but the WAV file was not found."
        end
        return true, 100, audioPath, nil
    end

    function R:cancelAudioRender()
        if not R.renderState then
            self:returnToEditPage()
            return
        end
        pcall(function()
            R.renderState.context.project:StopRendering()
        end)
        self:restoreRenderState()
        self:returnToEditPage()
    end

    function R:readActiveSubtitleTrack()
        local context = self:getContext()
        if not context then
            return nil
        end
        local rate, rateError = self:getFrameRate(context)
        if not rate then
            return nil, rateError
        end
        local timelineStartFrame = tonumber(context.timeline:GetStartFrame() or 0) or 0
        local trackCount = tonumber(context.timeline:GetTrackCount("subtitle") or 0) or 0
        local secondsPerFrame = rate.denominator / rate.numerator
        for trackIndex = 1, trackCount do
            if context.timeline:GetIsTrackEnabled("subtitle", trackIndex) == true then
                local items = context.timeline:GetItemListInTrack("subtitle", trackIndex) or {}
                if #items > 0 then
                    local blocks = {}
                    for itemIndex, item in ipairs(items) do
                        local itemReadOk, startValue, endValue, nameValue = pcall(function()
                            return item:GetStart(), item:GetEnd(), item:GetName()
                        end)
                        if not itemReadOk then
                            return nil, "Resolve could not read subtitle item " .. tostring(itemIndex)
                                .. " on track " .. tostring(trackIndex) .. ": " .. tostring(startValue)
                        end
                        local startFrame = tonumber(startValue)
                        local endFrame = tonumber(endValue)
                        if not startFrame or not endFrame or endFrame < startFrame then
                            return nil, "Resolve returned an invalid frame range for subtitle item "
                                .. tostring(itemIndex) .. " on track " .. tostring(trackIndex) .. "."
                        end
                        blocks[#blocks + 1] = {
                            start = math.max(0, (startFrame - timelineStartFrame) * secondsPerFrame),
                            ["end"] = math.max(0, (endFrame - timelineStartFrame) * secondsPerFrame),
                            text = tostring(nameValue or "")
                        }
                    end
                    table.sort(blocks, function(left, right)
                        if left.start == right.start then
                            return left["end"] < right["end"]
                        end
                        return left.start < right.start
                    end)
                    return {
                        blocks = blocks,
                        subtitleStartFrame = timelineStartFrame,
                        trackIndex = trackIndex
                    }
                end
            end
        end
        return nil
    end

    function R:importSrt(path, startFrame)
        local context, contextError = self:getContext()
        if not context then
            return false, contextError
        end
        if not context.mediaPool then
            return false, "Resolve Media Pool is unavailable."
        end
        local trackCount = tonumber(context.timeline:GetTrackCount("subtitle") or 0) or 0
        local previousStates = {}
        local target = nil
        local function restoreTrackStates()
            for index = 1, trackCount do
                context.timeline:SetTrackEnable("subtitle", index, previousStates[index] == true)
            end
            if target and target > trackCount then
                context.timeline:SetTrackEnable("subtitle", target, false)
            end
        end
        for index = 1, trackCount do
            previousStates[index] = context.timeline:GetIsTrackEnabled("subtitle", index) == true
            local items = context.timeline:GetItemListInTrack("subtitle", index) or {}
            if not target and #items == 0 then
                target = index
            end
        end
        for index = 1, trackCount do
            if context.timeline:SetTrackEnable("subtitle", index, false) ~= true then
                restoreTrackStates()
                return false, "Resolve could not disable the existing subtitle tracks."
            end
        end
        if not target then
            if context.timeline:AddTrack("subtitle") ~= true then
                restoreTrackStates()
                return false, "Resolve could not add a subtitle track."
            end
            target = tonumber(context.timeline:GetTrackCount("subtitle") or 0) or 0
        end
        if context.timeline:SetTrackEnable("subtitle", target, true) ~= true then
            restoreTrackStates()
            return false, "Resolve could not enable the target subtitle track."
        end
        local added = context.mediaPool:ImportMedia({ path })
        local mediaItem = type(added) == "table" and added[#added] or nil
        if not mediaItem then
            restoreTrackStates()
            return false, "Resolve could not import the generated SRT into the Media Pool."
        end
        local targetFrame = tonumber(startFrame)
            or tonumber(context.timeline:GetStartFrame() or 0)
            or 0
        local rate, rateError = self:getFrameRate(context)
        if not rate then
            restoreTrackStates()
            return false, rateError
        end
        if context.timeline:SetCurrentTimecode(framesToTimecode(targetFrame, rate)) ~= true then
            restoreTrackStates()
            return false, "Resolve could not move the playhead to the subtitle insertion point."
        end
        local appended = context.mediaPool:AppendToTimeline({ mediaItem })
        if type(appended) ~= "table" or #appended == 0 then
            restoreTrackStates()
            return false, "Resolve could not append the SRT to the timeline."
        end
        return true
    end

    App.Resolve = R
end

-- 6. UI
do
    local UI = {
        items = nil,
        window = nil,
        blocks = {},
        selectedIndex = nil,
        suppressEditor = false,
        tick = 0,
        currentLanguage = "en",
        downloadSourceWindow = nil,
        downloadSourceItems = nil,
        diagnosticsWindow = nil,
        diagnosticsItems = nil,
        _statusKey = "ready_help",
        _statusArgs = {},
        _lastDownloadStatus = nil,
        _updateInfo = nil,
        _updateCheckPending = false,
        _startupPhase = "idle",
        _startupUpdateTick = 0,
        _runtimeLaunchStartedAt = nil,
        _runtimeStartFailureReported = false,
        _startupSubtitleLoadPending = false,
        _subtitle_blocks_state = {},
        subtitleStartFrame = nil,
        _find_query = "",
        _find_matches = {},
        _find_index = 0,
        _current_match_highlight = nil,
        _sticky_highlights = {},
        _find_rows = 0,
        _find_occurrences = 0,
        _suppress_tree_event = false,
        _findDebounceTicks = 0,
        _treePopulation = nil,
        _selectedTreeItem = nil,
        _findHighlightedRows = {},
        _findHighlightPopulation = nil
    }
    local Core = App.Core
    local Config = App.Config
    local Utils = App.Utils
    local unpackValues = table.unpack or unpack
    local FIND_HIGHLIGHT_COLOR = { R = 0.40, G = 0.40, B = 0.40, A = 0.60 }
    local TRANSPARENT_COLOR = { R = 0.0, G = 0.0, B = 0.0, A = 0.0 }
    -- Keep 1% for reading and importing the completed subtitle result.
    local SUBTITLE_RENDER_PROGRESS_MAX = 15
    local SUBTITLE_RUNTIME_PROGRESS_SPAN = 84

    local STATUS_MESSAGES = {
        ready_help = {
            cn = "就绪：选语言后创建字幕。",
            en = "Ready: choose a language, then create subtitles."
        },
        enter_find_text = { cn = "请输入查找文字。", en = "Enter text to find." },
        matches_rows_occ = { cn = "%d 条字幕，%d 处匹配。", en = "%d rows, %d matches." },
        no_find_results = { cn = "未找到匹配字幕。", en = "No matches found." },
        match_progress = { cn = "第 %d / %d 个结果。", en = "Match %d / %d." },
        replace_no_find = { cn = "请先输入查找文字。", en = "Enter text to find first." },
        no_replace = { cn = "没有可替换内容。", en = "Nothing to replace." },
        replace_done = { cn = "已替换 %d 处。", en = "Replaced %d occurrence(s)." },
        runtime_starting = { cn = "正在启动本地识别组件…", en = "Starting local recognition…" },
        runtime_start_failed = { cn = "启动失败，请打开诊断。", en = "Start failed. Open diagnostics." },
        runtime_missing_help = { cn = "组件缺失，请重新安装。", en = "Component missing. Reinstall the app." },
        request_write_failed = { cn = "任务文件写入失败。", en = "Could not write the task file." },
        model_source_invalid = { cn = "下载源无效。", en = "Invalid download source." },
        model_download_queued = { cn = "%s：准备下载。", en = "%s: preparing download." },
        download_connecting = { cn = "正在连接 %s…", en = "Connecting to %s…" },
        download_preparing = { cn = "正在准备模型下载…", en = "Preparing model download…" },
        download_active = { cn = "下载中，进度见按钮。", en = "Downloading. Progress is on the button." },
        download_verifying = { cn = "正在验证模型…", en = "Verifying model…" },
        model_ready = { cn = "模型就绪，可以创建字幕。", en = "Model ready. You can create subtitles." },
        model_download_failed = { cn = "下载失败，请检查网络。", en = "Download failed. Check your network." },
        model_required = { cn = "请先下载模型。", en = "Download the model first." },
        timeline_prepare_failed = { cn = "无法读取当前时间线。", en = "Could not read the current timeline." },
        render_start_failed = { cn = "无法导出时间线音频。", en = "Could not export timeline audio." },
        subtitle_progress = { cn = "正在创建字幕 %d%%…", en = "Creating subtitles %d%%…" },
        no_task = { cn = "当前没有任务。", en = "No task is running." },
        render_cancelled = { cn = "音频导出已取消。", en = "Audio export cancelled." },
        cancel_requested = { cn = "正在取消任务…", en = "Cancelling task…" },
        srt_import_failed = { cn = "字幕已生成，但导入失败。", en = "Subtitles created, but import failed." },
        subtitles_created = { cn = "字幕已创建并导入 · 100%", en = "Subtitles created and imported · 100%" },
        subtitles_created_partial = {
            cn = "字幕已创建并导入 · 100%%，但有 %d 段语音未识别。",
            en = "Subtitles created and imported · 100%%, but %d speech region(s) were not recognized."
        },
        runtime_invalid_state = { cn = "任务状态异常，请打开诊断。", en = "Invalid task state. Open diagnostics." },
        result_unreadable = { cn = "结果无法读取，请打开诊断。", en = "Could not read the result. Open diagnostics." },
        task_cancelled = { cn = "任务已取消。", en = "Task cancelled." },
        runtime_error = { cn = "任务失败，请打开诊断。", en = "Task failed. Open diagnostics." },
        render_failed = { cn = "音频导出失败。", en = "Audio export failed." },
        edited_srt_write_failed = { cn = "编辑字幕保存失败。", en = "Could not save edited subtitles." },
        edited_subtitles_imported = { cn = "编辑字幕已导入。", en = "Edited subtitles imported." },
        edited_subtitles_import_failed = { cn = "编辑字幕导入失败。", en = "Edited subtitle import failed." },
        diagnostics_copied = { cn = "诊断信息已复制。", en = "Diagnostics copied." }
    }

    local TRANSLATIONS = {
        cn = {
            TitleLabel = "从音频创建字幕",
            TreeTitleLabel = "字幕编辑",
            ModelLabel = "模型",
            LangLabel = "语言",
            MaxCharsLabel = "每行最大字符",
            RemoveGaps = "字幕无间隙",
            TrimPunctuation = "句末无标点",
            PromptLabel = "短语列表 / 提示",
            DownloadModels = "模型下载",
            DownloadSourceWindowTitle = "选择模型下载源",
            DownloadSourceMessage = "中国用户建议选择 ModelScope，非中国用户建议选择 Hugging Face。请选择下载来源。",
            DiagnosticsWindowTitle = "DaVinci ASR 诊断",
            DiagnosticsVersion = "DaVinci ASR 版本",
            DiagnosticsProtocol = "协议",
            DiagnosticsRuntime = "运行时",
            DiagnosticsOS = "操作系统",
            DiagnosticsArchitecture = "架构",
            DiagnosticsBackend = "后端",
            DiagnosticsGPU = "GPU",
            DiagnosticsDtype = "数据类型",
            DiagnosticsModel = "模型",
            DiagnosticsLastJob = "上次任务",
            DiagnosticsCachePolicy = "缓存策略",
            DiagnosticsAudioDuration = "音频时长",
            DiagnosticsTotal = "总耗时",
            DiagnosticsRTF = "实时系数（RTF）",
            DiagnosticsPeakRSS = "峰值内存",
            DiagnosticsOffline = "离线",
            DiagnosticsUnknown = "未知",
            DiagnosticsNone = "无",
            DiagnosticsSuccess = "成功",
            DiagnosticsModelsReady = "模型已就绪",
            DiagnosticsSelfTestSuccess = "自检成功",
            DiagnosticsCancelled = "已取消",
            DiagnosticsError = "错误",
            DiagnosticsRuntimeError = "运行时错误",
            DiagnosticsPending = "初始化中",
            DiagnosticsCacheProcessors = "仅缓存处理器",
            DiagnosticsCacheSingle = "单模型",
            DiagnosticsCacheDual = "双模型",
            DiagnosticsSeconds = "秒",
            DiagnosticsMilliseconds = "毫秒",
            DiagnosticsBytes = "字节",
            CreateSubtitles = "创建字幕",
            Cancel = "取消",
            UpdateSubtitles = "更新字幕",
            FindButton = "查找下一个",
            SingleReplaceButton = "替换",
            AllReplaceButton = "全部替换",
            CopyrightButton = "更多功能 © 2026 HEIBA 版权所有",
            FindPlaceholder = "查找文本",
            ReplacePlaceholder = "替换文本",
            PromptPlaceholder = "人名、术语和上下文提示…",
            RuntimeOffline = "Runtime 离线",
            RuntimeMissing = "Runtime 未安装",
            Installed = "已安装",
            NotInstalled = "未安装"
        },
        en = {
            TitleLabel = "Create subtitles from audio",
            TreeTitleLabel = "Subtitle Editor",
            ModelLabel = "Models",
            LangLabel = "Language",
            MaxCharsLabel = "Max Characters per Subtitle",
            RemoveGaps = "No Gaps",
            TrimPunctuation = "No End Marks",
            PromptLabel = "Phrases / Prompt",
            DownloadModels = "Download Models",
            DownloadSourceWindowTitle = "Choose Model Download Source",
            DownloadSourceMessage = "ModelScope is recommended in China; Hugging Face is recommended outside China. Choose a download source.",
            DiagnosticsWindowTitle = "DaVinci ASR Diagnostics",
            DiagnosticsVersion = "DaVinci ASR Version",
            DiagnosticsProtocol = "Protocol",
            DiagnosticsRuntime = "Runtime",
            DiagnosticsOS = "OS",
            DiagnosticsArchitecture = "Architecture",
            DiagnosticsBackend = "Backend",
            DiagnosticsGPU = "GPU",
            DiagnosticsDtype = "Data Type",
            DiagnosticsModel = "Model",
            DiagnosticsLastJob = "Last Job",
            DiagnosticsCachePolicy = "Cache Policy",
            DiagnosticsAudioDuration = "Audio Duration",
            DiagnosticsTotal = "Total Time",
            DiagnosticsRTF = "RTF",
            DiagnosticsPeakRSS = "Peak RSS",
            DiagnosticsOffline = "Offline",
            DiagnosticsUnknown = "Unknown",
            DiagnosticsNone = "None",
            DiagnosticsSuccess = "Success",
            DiagnosticsModelsReady = "Models Ready",
            DiagnosticsSelfTestSuccess = "Self-Test Passed",
            DiagnosticsCancelled = "Cancelled",
            DiagnosticsError = "Error",
            DiagnosticsRuntimeError = "Runtime Error",
            DiagnosticsPending = "Initializing",
            DiagnosticsCacheProcessors = "Processors Only",
            DiagnosticsCacheSingle = "Single Model",
            DiagnosticsCacheDual = "Dual Models",
            DiagnosticsSeconds = "s",
            DiagnosticsMilliseconds = "ms",
            DiagnosticsBytes = "bytes",
            CreateSubtitles = "Create Subtitles",
            Cancel = "Cancel",
            UpdateSubtitles = "Update Subtitles",
            FindButton = "Find Next",
            SingleReplaceButton = "Replace",
            AllReplaceButton = "Replace All",
            CopyrightButton = "More Features © 2026 by HEIBA",
            FindPlaceholder = "Find text",
            ReplacePlaceholder = "Replace with",
            PromptPlaceholder = "Names, terminology, and context…",
            RuntimeOffline = "Runtime Offline",
            RuntimeMissing = "Runtime Not Installed",
            Installed = "Installed",
            NotInstalled = "Not Installed"
        }
    }

    function UI.buildMainWindow()
        local ui = Core.ui
        return Core.dispatcher:AddWindow({
            ID = Config.WINDOW_ID,
            WindowTitle = Config.SCRIPT_NAME .. " " .. Config.SCRIPT_VERSION,
            Geometry = { 460, 240, 800, 500 },
            Spacing = 10,
            StyleSheet = "*{font-size:14px;}"
        }, ui:VGroup{
            ID = "Root",
            Spacing = 10,
            ui:HGroup{
                Weight = 1,
                ui:VGroup{
                    Weight = 35,
                    ui:Label{
                        ID = "TitleLabel",
                        Text = "从音频创建字幕",
                        Alignment = { AlignHCenter = true, AlignVCenter = true },
                        Weight = 0
                    },
                    ui:VGap(5),
                    ui:HGroup{
                        Weight = 0,
                        ui:Label{ ID = "ModelLabel", Text = "模型", Weight = 0.24 },
                        ui:ComboBox{ ID = "ModelCombo", Weight = 0.50 },
                        ui:Label{
                            ID = "ModelStatus",
                            Text = "状态：未安装",
                            Alignment = { AlignRight = true, AlignVCenter = true },
                            Weight = 0.26
                        }
                    },
                    ui:Button{ ID = "DownloadModels", Text = "模型下载", Weight = 0 },
                    ui:HGroup{
                        Weight = 0,
                        ui:Label{ ID = "LangLabel", Text = "语言", Weight = 0.4 },
                        ui:ComboBox{ ID = "LanguageCombo", Weight = 0.6 }
                    },
                    ui:HGroup{
                        Weight = 0,
                        ui:Label{ ID = "MaxCharsLabel", Text = "每行最大字符", Weight = 0.4 },
                        ui:SpinBox{
                            ID = "MaxChars",
                            Minimum = 1,
                            Maximum = 500,
                            Value = 42,
                            SingleStep = 1,
                            Weight = 0.6
                        }
                    },
                    ui:HGroup{
                        Weight = 0,
                        ui:CheckBox{ ID = "RemoveGaps", Text = "字幕无间隙", Checked = false, Weight = 0 },
                        ui:Label{ Text = "" },
                        ui:CheckBox{ ID = "TrimPunctuation", Text = "句末无标点", Checked = false, Weight = 0 }
                    },
                    ui:Label{ ID = "PromptLabel", Text = "短语列表 / 提示", Weight = 0 },
                    ui:TextEdit{ ID = "PromptEdit", PlaceholderText = "人名、术语和上下文提示…", Weight = 0.1 },
                    ui:Label{
                        ID = "StatusLabel",
                        Text = "就绪：选语言后创建字幕。",
                        WordWrap = false,
                        StyleSheet = "",
                        Alignment = { AlignHCenter = true, AlignVCenter = true },
                        Font = ui:Font{ PixelSize = 12 },
                        Weight = 0.1
                    },
                    ui:HGroup{
                        Weight = 0,
                        ui:Button{ ID = "CreateSubtitles", Text = "创建字幕", Weight = 0.72 },
                        ui:Button{ ID = "Cancel", Text = "取消", Enabled = false, Weight = 0.28 }
                    }
                },
                ui:VGroup{
                    Weight = 65,
                    ui:Label{
                        ID = "TreeTitleLabel",
                        Text = "字幕编辑",
                        Alignment = { AlignHCenter = true, AlignVCenter = true },
                        Weight = 0
                    },
                    ui:VGap(5),
                    ui:HGroup{
                        Weight = 0,
                        Spacing = 6,
                        ui:LineEdit{
                            ID = "FindInput",
                            PlaceholderText = "查找文本",
                            Events = { TextChanged = true, EditingFinished = true },
                            Weight = 1
                        },
                        ui:Button{ ID = "FindButton", Text = "查找下一个", Weight = 0 },
                        ui:LineEdit{ ID = "ReplaceInput", PlaceholderText = "替换文本", Weight = 1 },
                        ui:Button{ ID = "AllReplaceButton", Text = "全部替换", Weight = 0 },
                        ui:Button{ ID = "SingleReplaceButton", Text = "替换", Weight = 0 }
                    },
                    ui:Tree{
                        ID = "SubtitleTree",
                        ColumnCount = 4,
                        AlternatingRowColors = true,
                        SortingEnabled = false,
                        WordWrap = true,
                        UniformRowHeights = false,
                        HorizontalScrollMode = true,
                        FrameStyle = 1,
                        SelectionMode = "SingleSelection",
                        Weight = 1
                    },
                    ui:TextEdit{ ID = "SubtitleEditor", Weight = 0 },
                    ui:Button{ ID = "UpdateSubtitles", Text = "更新字幕", Weight = 0 }
                }
            },
            ui:HGroup{
                Weight = 0,
                Spacing = 6,
                ui:Button{
                    ID = "RuntimeStatusButton",
                    Text = "🔴 Runtime 未安装",
                    Alignment = { AlignLeft = true, AlignVCenter = true },
                    Font = ui:Font{ PixelSize = 12, Bold = true },
                    Flat = true,
                    MinimumSize = { 260, 30 },
                    Weight = 0
                },
                ui:Label{ Text = "", Weight = 1 },
                ui:Button{
                    ID = "CopyrightButton",
                    Text = "更多功能 © 2026 HEIBA 版权所有",
                    Alignment = { AlignRight = true, AlignVCenter = true },
                    Font = ui:Font{ PixelSize = 12, Bold = true },
                    Flat = true,
                    Weight = 0
                },
                ui:HGroup{
                    Weight = 0,
                    Spacing = 0,
                    ui:CheckBox{ ID = "LangEnCheckBox", Text = "EN", Checked = false, Weight = 0 },
                    ui:CheckBox{ ID = "LangCnCheckBox", Text = "中文", Checked = true, Weight = 0 }
                }
            }
        })
    end

    function UI.text(key)
        local language = TRANSLATIONS[UI.currentLanguage] or TRANSLATIONS.cn
        return language[key] or TRANSLATIONS.en[key] or key
    end

    function UI.sanitizeUserText(value)
        local replacement = UI.currentLanguage == "cn" and "字幕时间组件" or "subtitle timing component"
        return tostring(value or "")
            :gsub("Qwen3%-ForcedAligner%-0%.6B%-hf", replacement)
            :gsub("Qwen3 Forced Aligner", replacement)
            :gsub("Forced Aligner", replacement)
            :gsub("forced_aligner", replacement)
            :gsub("Aligner", replacement)
    end

    function UI.renderStatus()
        if not UI.items or not UI.items.StatusLabel then
            return
        end
        UI.items.StatusLabel.StyleSheet = ""
        if UI._statusKey == "ready_help" and type(UI._updateInfo) == "table" then
            local latest = Utils.trim(UI._updateInfo.latest)
            local current = Utils.trim(UI._updateInfo.current)
            if latest ~= "" then
                if current == "" then
                    current = UI.currentLanguage == "cn" and "未知" or "unknown"
                end
                UI.items.StatusLabel.Text = UI.currentLanguage == "cn"
                    and string.format("🔴 新版本 %s → %s，点击“更多功能”下载。", current, latest)
                    or string.format("🔴 Update %s → %s. Click More Features.", current, latest)
                UI.items.StatusLabel.StyleSheet = "color:#e05252; font-weight:bold;"
                return
            end
        end
        local messages = STATUS_MESSAGES[UI._statusKey] or STATUS_MESSAGES.ready_help
        local template = messages[UI.currentLanguage] or messages.cn
        local args = UI._statusArgs or {}
        local renderedArgs = {}
        for index, value in ipairs(args) do
            renderedArgs[index] = type(value) == "string" and UI.sanitizeUserText(value) or value
        end
        local ok, message = pcall(function()
            return #renderedArgs > 0 and string.format(template, unpackValues(renderedArgs)) or template
        end)
        UI.items.StatusLabel.Text = ok and message or template
    end

    function UI.setStatusKey(key, ...)
        UI._statusKey = STATUS_MESSAGES[key] and key or "ready_help"
        UI._statusArgs = { ... }
        UI.renderStatus()
    end

    function UI.beginSubtitleProgress()
        UI._subtitleOverallProgress = nil
        UI.setSubtitleProgress(0)
    end

    function UI.setSubtitleProgress(value)
        local progress = math.floor(math.max(0, math.min(99, tonumber(value) or 0)) + 0.5)
        if UI._subtitleOverallProgress ~= nil then
            progress = math.max(progress, UI._subtitleOverallProgress)
        end
        UI._subtitleOverallProgress = progress
        UI.setStatusKey("subtitle_progress", progress)
        return progress
    end

    function UI.updateRenderSubtitleProgress(value)
        local progress = math.max(0, math.min(100, tonumber(value) or 0))
        return UI.setSubtitleProgress(progress * SUBTITLE_RENDER_PROGRESS_MAX / 100)
    end

    function UI.updateRuntimeSubtitleProgress(value)
        local progress = math.max(0, math.min(100, tonumber(value) or 0))
        return UI.setSubtitleProgress(
            SUBTITLE_RENDER_PROGRESS_MAX + progress * SUBTITLE_RUNTIME_PROGRESS_SPAN / 100
        )
    end

    function UI.applyUpdateManifest(result)
        if type(result) ~= "table" then
            return false
        end
        local latest = Utils.trim(result.latest)
        local current = Utils.trim(Config.SCRIPT_VERSION)
        if latest == "" or not Utils.versionIsNewer(latest, current) then
            UI._updateInfo = nil
            UI.renderStatus()
            return false
        end
        UI._updateInfo = { latest = latest, current = current }
        UI.renderStatus()
        return true
    end

    function UI.checkForUpdates()
        if UI._updateCheckPending then
            return false
        end
        local started, startError = Utils.startUpdateManifestFetch(Config.SCRIPT_NAME)
        if not started then
            Utils.logError("UPDATE_CHECK_START_FAILED", startError)
            return false
        end
        UI._updateCheckPending = true
        return false
    end

    function UI.pollUpdateCheck()
        if not UI._updateCheckPending or not Utils.fileExists(Config.UPDATE_DONE_FILE) then
            return false
        end
        UI._updateCheckPending = false
        if not Utils.fileExists(Config.UPDATE_RESULT_FILE) then
            os.remove(Config.UPDATE_DONE_FILE)
            return false
        end
        local result, resultError = Utils.readJson(Config.UPDATE_RESULT_FILE)
        local valid = type(result) == "table"
        os.remove(Config.UPDATE_RESULT_FILE)
        os.remove(Config.UPDATE_DONE_FILE)
        if not valid then
            Utils.logError("UPDATE_CHECK_FAILED", resultError)
            return false
        end
        return UI.applyUpdateManifest(result)
    end

    function UI.openMoreFeatures()
        local opened, openError = Utils.openExternalUrl(Config.MORE_FEATURES_URL)
        if not opened then
            Utils.logError("OPEN_WEBSITE_FAILED", openError)
        end
        return opened
    end

    function UI.guard(code, callback)
        return function(...)
            local ok, result = pcall(callback, ...)
            if not ok then
                Utils.logError(code, result)
                return nil
            end
            return result
        end
    end

    function UI.populateLanguageCombo()
        if not UI.items or not UI.items.LanguageCombo then
            return
        end
        local selected = tonumber(UI.items.LanguageCombo.CurrentIndex or 0) or 0
        UI.items.LanguageCombo:Clear()
        UI.items.LanguageCombo:AddItems(Config.LANGUAGE_LABELS[UI.currentLanguage] or Config.LANGUAGE_LABELS.cn)
        UI.items.LanguageCombo.CurrentIndex = math.max(0, math.min(#Config.LANGUAGES - 1, selected))
    end

    function UI.applyLanguage(language)
        UI.currentLanguage = language == "en" and "en" or "cn"
        local textIds = {
            "TitleLabel", "TreeTitleLabel", "ModelLabel",
            "LangLabel", "MaxCharsLabel", "RemoveGaps",
            "TrimPunctuation", "PromptLabel", "DownloadModels",
            "CreateSubtitles", "Cancel", "UpdateSubtitles", "FindButton",
            "SingleReplaceButton", "AllReplaceButton", "CopyrightButton"
        }
        for _, id in ipairs(textIds) do
            if UI.items[id] then
                UI.items[id].Text = UI.text(id)
            end
        end
        UI.items.FindInput.PlaceholderText = UI.text("FindPlaceholder")
        UI.items.ReplaceInput.PlaceholderText = UI.text("ReplacePlaceholder")
        UI.items.PromptEdit.PlaceholderText = UI.text("PromptPlaceholder")
        UI.items.LangEnCheckBox.Checked = UI.currentLanguage == "en"
        UI.items.LangCnCheckBox.Checked = UI.currentLanguage == "cn"
        UI.populateLanguageCombo()
        UI.refreshRuntimeStatus()
        if Core.job and Core.job.action == "download_models" and UI._lastDownloadStatus then
            UI.items.DownloadModels.Text = UI.downloadButtonText(UI._lastDownloadStatus)
        end
        UI.refreshDownloadSourceWindowLanguage()
        UI.refreshDiagnosticsWindowLanguage()
        UI.renderStatus()
    end

    function UI.formatBytes(value)
        local bytes = math.max(0, tonumber(value) or 0)
        if bytes >= 1000 * 1000 * 1000 then
            return string.format("%.2f GB", bytes / (1000 * 1000 * 1000))
        end
        if bytes >= 1000 * 1000 then
            return string.format("%.0f MB", bytes / (1000 * 1000))
        end
        if bytes >= 1000 then
            return string.format("%.0f KB", bytes / 1000)
        end
        return string.format("%d B", math.floor(bytes))
    end

    function UI.formatRate(value)
        local bytes = math.max(0, tonumber(value) or 0)
        if bytes >= 1000 * 1000 * 1000 then
            return string.format("%.2f GB/s", bytes / (1000 * 1000 * 1000))
        end
        if bytes >= 1000 * 1000 then
            return string.format("%.1f MB/s", bytes / (1000 * 1000))
        end
        if bytes >= 1000 then
            return string.format("%.1f KB/s", bytes / 1000)
        end
        return string.format("%d B/s", math.floor(bytes))
    end

    function UI.downloadSourceName(source)
        if source == "modelscope" then
            return "ModelScope"
        end
        if source == "huggingface" then
            return "Hugging Face"
        end
        return tostring(source or "")
    end

    function UI.downloadButtonText(status)
        status = type(status) == "table" and status or {}
        local downloadStatus = tostring(status.download_status or "")
        local source = UI.downloadSourceName(status.download_source)
        local percent = math.max(0, math.min(100, tonumber(status.progress) or 0))
        local downloaded = tonumber(status.downloaded_bytes) or 0
        local total = tonumber(status.total_bytes) or 0
        local speed = tonumber(status.download_speed_bps) or 0
        local base = UI.text("DownloadModels")
        if downloadStatus == "connecting" then
            if source ~= "" then
                return base .. " · " .. (UI.currentLanguage == "cn"
                    and ("正在连接 " .. source) or ("Connecting to " .. source))
            else
                return base .. " · " .. (UI.currentLanguage == "cn" and "正在准备" or "Preparing")
            end
        elseif downloadStatus == "fallback" then
            return base .. " · " .. (UI.currentLanguage == "cn" and "正在切换下载源" or "Switching source")
        elseif downloadStatus == "downloading" then
            local sizeText = UI.formatBytes(downloaded)
            if total > 0 then
                sizeText = sizeText .. " / " .. UI.formatBytes(total)
            end
            return string.format(
                "%s · %d%% · %s · %s",
                base,
                math.floor(percent + 0.5),
                UI.formatRate(speed),
                sizeText
            )
        elseif downloadStatus == "verifying" then
            return base .. " · " .. (UI.currentLanguage == "cn" and "正在验证" or "Verifying")
        elseif downloadStatus == "complete" then
            return UI.currentLanguage == "cn" and "模型下载完成" or "Model Download Complete"
        elseif downloadStatus == "failed" then
            return UI.currentLanguage == "cn" and "模型下载失败 · 点击重试" or "Model Download Failed · Click to Retry"
        elseif downloadStatus == "cancelled" then
            return UI.currentLanguage == "cn" and "模型下载已取消 · 点击重试" or "Model Download Cancelled · Click to Retry"
        end
        return base
    end

    function UI.updateDownloadButton(status)
        if not UI.items or not UI.items.DownloadModels then
            return
        end
        UI._lastDownloadStatus = type(status) == "table" and status or nil
        UI.items.DownloadModels.Text = UI.downloadButtonText(status)
    end

    function UI.setIdleDownloadButton()
        if not UI.items or not UI.items.DownloadModels then
            return
        end
        if Core.job and Core.job.action == "download_models" then
            return
        end
        UI._lastDownloadStatus = nil
        UI.items.DownloadModels.Text = UI.text("DownloadModels")
    end

    function UI.updateFindStatus(key, ...)
        if not key then
            UI.setStatusKey("ready_help")
            return
        end
        UI.setStatusKey(key, ...)
    end

    function UI.collectSettings()
        local languageIndex = tonumber(UI.items.LanguageCombo.CurrentIndex or 0) or 0
        return {
            language = Config.LANGUAGES[languageIndex + 1] or "Auto",
            prompt = UI.items.PromptEdit.PlainText or "",
            max_chars = tonumber(UI.items.MaxChars.Value) or 42,
            remove_gaps = UI.items.RemoveGaps.Checked == true,
            trim_end_punctuation = UI.items.TrimPunctuation.Checked == true,
            ui_language = UI.currentLanguage
        }
    end

    function UI.applySettings(values)
        local index = 0
        for candidate, language in ipairs(Config.LANGUAGES) do
            if language == values.language then
                index = candidate - 1
                break
            end
        end
        UI.items.LanguageCombo.CurrentIndex = index
        UI.items.PromptEdit.PlainText = values.prompt or ""
        UI.items.MaxChars.Value = tonumber(values.max_chars) or 42
        UI.items.RemoveGaps.Checked = values.remove_gaps == true
        UI.items.TrimPunctuation.Checked = values.trim_end_punctuation == true
        UI.applyLanguage(values.ui_language)
    end

    function UI.modelsReady(models)
        models = type(models) == "table" and models or {}
        return models.asr == "Ready" and models.forced_aligner == "Ready"
    end

    function UI.setModelStatus(models)
        if not UI.items or not UI.items.ModelStatus then
            return
        end
        local state = UI.modelsReady(models) and UI.text("Installed") or UI.text("NotInstalled")
        UI.items.ModelStatus.Text = (UI.currentLanguage == "cn" and "状态：" or "Status: ") .. state
    end

    function UI.refreshRuntimeStatus()
        local status = Utils.runtimeStatus()
        if not Utils.runtimeHeartbeatFresh(status) then
            Core.runtimeStatus = nil
            local runtimeText = Utils.fileExists(Config.RUNTIME_EXECUTABLE)
                and UI.text("RuntimeOffline") or UI.text("RuntimeMissing")
            UI.items.RuntimeStatusButton.Text = "🔴 " .. runtimeText
            local staleModels = type(status) == "table" and status.model_status or {}
            UI.setModelStatus(staleModels)
            UI.setIdleDownloadButton()
            if UI._runtimeLaunchStartedAt
                and not UI._runtimeStartFailureReported
                and os.time() - UI._runtimeLaunchStartedAt >= 12 then
                UI._runtimeStartFailureReported = true
                Utils.logError("RUNTIME_START_TIMEOUT", "Runtime did not publish a healthy heartbeat within 12 seconds.")
                UI.setStatusKey("runtime_start_failed")
            end
            return false
        end
        Core.runtimeStatus = status
        UI._runtimeLaunchStartedAt = nil
        UI._runtimeStartFailureReported = false
        local hardware = type(status.hardware) == "table" and status.hardware or {}
        local backend = tostring(status.backend or hardware.backend or "Unknown")
        if Config.RUNTIME_KIND == "development" then
            backend = backend .. (UI.currentLanguage == "cn" and " · 开发 Runtime" or " · Dev Runtime")
        end
        UI.items.RuntimeStatusButton.Text = "🟢 " .. backend
        local models = type(status.model_status) == "table" and status.model_status or {}
        UI.setModelStatus(models)
        UI.setIdleDownloadButton()
        if not Core.job and UI._statusKey == "runtime_starting" then
            UI.setStatusKey("ready_help")
        end
        return true
    end

    function UI.ensureRuntime()
        if UI.refreshRuntimeStatus() then
            return true
        end
        if not Utils.fileExists(Config.RUNTIME_EXECUTABLE) then
            Utils.logError("RUNTIME_MISSING", "The private Runtime executable was not found.")
            UI.setStatusKey("runtime_missing_help")
            return false
        end
        local launched, launchError = Utils.launchRuntime()
        if not launched then
            Utils.logError("RUNTIME_LAUNCH_FAILED", launchError)
            UI.setStatusKey("runtime_start_failed", launchError)
            return false
        end
        UI._runtimeLaunchStartedAt = os.time()
        UI._runtimeStartFailureReported = false
        UI.setStatusKey("runtime_starting")
        return true
    end

    function UI.jobFiles(jobId)
        local directory = Utils.joinPath(Config.IPC_DIR, jobId)
        return {
            directory = directory,
            request = Utils.joinPath(directory, "request.json"),
            status = Utils.joinPath(directory, "status.json"),
            result = Utils.joinPath(directory, "result.json"),
            cancel = Utils.joinPath(directory, "cancel.flag"),
            ack = Utils.joinPath(directory, "ack.flag")
        }
    end

    function UI.submitRequest(request, action)
        local files = UI.jobFiles(request.job_id)
        Utils.ensureDir(files.directory)
        local written, writeError = Utils.atomicWriteJson(files.request, request)
        if not written then
            Utils.logError("IPC_REQUEST_WRITE_FAILED", "action=" .. tostring(action) .. "; " .. tostring(writeError))
            UI.setStatusKey("request_write_failed", tostring(writeError))
            return false
        end
        Core.job = {
            id = request.job_id,
            action = action,
            phase = "waiting",
            files = files,
            lastState = "",
            statusReadFailureReported = false
        }
        UI.items.Cancel.Enabled = true
        UI.items.CreateSubtitles.Enabled = false
        UI.items.DownloadModels.Enabled = false
        return true
    end

    function UI.submitModelDownload(source)
        if source ~= "huggingface" and source ~= "modelscope" then
            Utils.logError("MODEL_SOURCE_INVALID", "source=" .. tostring(source))
            UI.setStatusKey("model_source_invalid")
            return
        end
        if not Utils.fileExists(Config.RUNTIME_EXECUTABLE) then
            Utils.logError("RUNTIME_MISSING", "Model download requires the private Runtime executable.")
            UI.setStatusKey("runtime_missing_help")
            return
        end
        if not UI.ensureRuntime() then
            return
        end
        local jobId = Utils.generateJobId("models")
        local submitted = UI.submitRequest({
            protocol = Config.PROTOCOL_VERSION,
            job_id = jobId,
            action = "download_models",
            audio_path = "",
            language = "Auto",
            prompt = "",
            ui_language = UI.currentLanguage,
            download_source = source,
            subtitle = {
                max_chars = 42,
                remove_gaps = false,
                trim_end_punctuation = false
            },
            timeline = { fps = "24", start_frame = 0 }
        }, "download_models")
        if submitted then
            UI.updateDownloadButton({
                download_status = "connecting",
                download_source = source
            })
            local sourceName = UI.downloadSourceName(source)
            UI.setStatusKey("model_download_queued", sourceName)
        end
    end

    function UI.refreshDownloadSourceWindowLanguage()
        if not UI.downloadSourceWindow then
            return false
        end
        UI.downloadSourceWindow.WindowTitle = UI.text("DownloadSourceWindowTitle")
        if UI.downloadSourceItems and UI.downloadSourceItems.DownloadSourceMessage then
            UI.downloadSourceItems.DownloadSourceMessage.Text = UI.text("DownloadSourceMessage")
        end
        return true
    end

    function UI.showDownloadSourceWindow()
        if UI.downloadSourceWindow then
            UI.refreshDownloadSourceWindowLanguage()
            UI.downloadSourceWindow:Show()
            return
        end
        local dialog = Core.dispatcher:AddWindow({
            ID = "DaVinciASRDownloadSource",
            WindowTitle = UI.text("DownloadSourceWindowTitle"),
            Geometry = { 560, 350, 620, 170 },
            Spacing = 12,
            WindowFlags = { Window = true, WindowStaysOnTopHint = true }
        }, Core.ui:VGroup{
            Core.ui:Label{
                ID = "DownloadSourceMessage",
                Text = UI.text("DownloadSourceMessage"),
                WordWrap = true,
                Weight = 1
            },
            Core.ui:HGroup{
                Weight = 0,
                Core.ui:Button{
                    ID = "DownloadFromHuggingFace",
                    Text = "Hugging Face"
                },
                Core.ui:Button{
                    ID = "DownloadFromModelScope",
                    Text = "ModelScope"
                }
            }
        })
        dialog.On.DownloadFromHuggingFace.Clicked = function()
            dialog:Hide()
            UI.submitModelDownload("huggingface")
        end
        dialog.On.DownloadFromModelScope.Clicked = function()
            dialog:Hide()
            UI.submitModelDownload("modelscope")
        end
        dialog.On.DaVinciASRDownloadSource.Close = function()
            dialog:Hide()
        end
        UI.downloadSourceWindow = dialog
        UI.downloadSourceItems = dialog:GetItems()
        UI.refreshDownloadSourceWindowLanguage()
        dialog:Show()
    end

    function UI.startDownloadModels()
        App.Settings:save(UI.collectSettings())
        UI.showDownloadSourceWindow()
    end

    function UI.startCreateSubtitles()
        local settings = UI.collectSettings()
        App.Settings:save(settings)
        if not UI.ensureRuntime() then
            return
        end
        local runtime = Core.runtimeStatus or {}
        local models = runtime.model_status or {}
        if not UI.modelsReady(models) then
            UI.setStatusKey("model_required")
            return
        end
        UI.beginSubtitleProgress()
        local jobId = Utils.generateJobId("asr")
        local cacheCallOk, cacheState, cacheError = pcall(function()
            return App.Resolve:getAudioCacheState(jobId)
        end)
        if not cacheCallOk or not cacheState then
            App.Resolve:returnToEditPage()
            local detail = cacheCallOk and cacheError or tostring(cacheState)
            Utils.logError("TIMELINE_PREPARE_FAILED", detail)
            UI._subtitleOverallProgress = nil
            UI.setStatusKey("timeline_prepare_failed", detail)
            return
        end
        Core.job = {
            id = jobId,
            action = "transcribe",
            phase = "preparing",
            settings = settings,
            renderState = cacheState,
            files = UI.jobFiles(jobId)
        }
        local cachedAudioPath = App.Resolve:existingAudioPath(cacheState)
        if cachedAudioPath then
            App.Resolve:returnToEditPage()
            UI.submitRenderedAudio(cachedAudioPath)
            return
        end
        local renderCallOk, renderState, renderError = pcall(function()
            return App.Resolve:startAudioRender(cacheState)
        end)
        if not renderCallOk or not renderState then
            App.Resolve:discardAudioCache(cacheState)
            App.Resolve:returnToEditPage()
            Core.job = nil
            local detail = renderCallOk and renderError or tostring(renderState)
            Utils.logError("RENDER_START_FAILED", detail)
            UI._subtitleOverallProgress = nil
            UI.setStatusKey("render_start_failed", detail)
            return
        end
        Core.job.phase = "rendering"
        Core.job.renderState = renderState
        UI.items.Cancel.Enabled = true
        UI.items.CreateSubtitles.Enabled = false
        UI.items.DownloadModels.Enabled = false
        UI.updateRenderSubtitleProgress(0)
    end

    function UI.submitRenderedAudio(audioPath)
        local job = Core.job
        if not job then
            return
        end
        App.Resolve:returnToEditPage()
        local request = {
            protocol = Config.PROTOCOL_VERSION,
            job_id = job.id,
            action = "transcribe",
            audio_path = audioPath,
            language = job.settings.language,
            prompt = job.settings.prompt,
            ui_language = UI.currentLanguage,
            subtitle = {
                max_chars = job.settings.max_chars,
                remove_gaps = job.settings.remove_gaps,
                trim_end_punctuation = job.settings.trim_end_punctuation
            },
            timeline = {
                fps = job.renderState.rate.protocol,
                start_frame = job.renderState.subtitleStartFrame or job.renderState.startFrame
            }
        }
        local renderState = job.renderState
        if UI.submitRequest(request, "transcribe") then
            Core.job.renderState = renderState
            Core.job.audioPath = audioPath
            UI.updateRuntimeSubtitleProgress(0)
        else
            App.Resolve:discardAudioCache(renderState)
            Core.job = nil
            UI._subtitleOverallProgress = nil
            UI.items.Cancel.Enabled = false
            UI.items.CreateSubtitles.Enabled = true
            UI.items.DownloadModels.Enabled = true
        end
    end

    function UI.finishJob(statusKey, ...)
        local args = { ... }
        Core.job = nil
        UI._subtitleOverallProgress = nil
        UI.items.Cancel.Enabled = false
        UI.items.CreateSubtitles.Enabled = true
        UI.items.DownloadModels.Enabled = true
        UI.refreshRuntimeStatus()
        UI.setStatusKey(statusKey or "ready_help", unpackValues(args))
    end

    function UI.acknowledgeJob(job)
        if not job or not job.files or not job.files.ack then
            return false
        end
        local written, writeError = Utils.atomicWriteText(job.files.ack, "ack\n")
        if not written then
            Utils.logError("IPC_ACK_WRITE_FAILED", writeError)
        end
        return written
    end

    function UI.cancelCurrentJob()
        App.Settings:save(UI.collectSettings())
        local job = Core.job
        if not job then
            UI.setStatusKey("no_task")
            return
        end
        Utils.ensureDir(job.files.directory)
        local cancelled, cancelError = Utils.atomicWriteText(job.files.cancel, "cancel\n")
        if not cancelled then
            Utils.logError("CANCEL_FLAG_WRITE_FAILED", cancelError)
        end
        if job.phase == "rendering" then
            App.Resolve:cancelAudioRender()
            App.Resolve:discardAudioCache(job.renderState)
            App.Resolve:returnToEditPage()
            UI.finishJob("render_cancelled")
            return
        end
        UI.setStatusKey("cancel_requested")
    end

    function UI._treeBatchBounds(total, startIndex, batchSize)
        local first = math.max(1, tonumber(startIndex) or 1)
        local last = math.min(math.max(0, tonumber(total) or 0), first + math.max(1, tonumber(batchSize) or 1) - 1)
        return first, last
    end

    function UI._populateTreeBatch()
        local state = UI._treePopulation
        if not state then
            return true
        end
        local first, last = UI._treeBatchBounds(
            #UI._subtitle_blocks_state,
            state.nextIndex,
            state.batchSize
        )
        for index = first, last do
            local block = UI._subtitle_blocks_state[index]
            local item = state.tree:NewItem()
            item.Text[0] = tostring(index)
            item.Text[1] = App.Resolve:secondsToTimecode(block.start, state.timecodeContext)
            item.Text[2] = App.Resolve:secondsToTimecode(block["end"], state.timecodeContext)
            item.Text[3] = tostring(block.text or ""):gsub("\n", " ")
            state.tree:AddTopLevelItem(item)
        end
        state.nextIndex = last + 1
        if state.nextIndex > #UI._subtitle_blocks_state then
            UI._treePopulation = nil
            if UI._find_query ~= "" then
                UI._refresh_find_matches()
            end
            return true
        end
        return false
    end

    function UI.refreshTree(batchSize)
        local tree = UI.items.SubtitleTree
        UI._reset_find_state(false)
        UI._selectedTreeItem = nil
        UI._treePopulation = nil
        tree:Clear()
        tree:SetHeaderLabels({ "#", "Start", "End", "Subtitle" })
        tree.ColumnWidth[0] = 45
        tree.ColumnWidth[1] = 50
        tree.ColumnWidth[2] = 50
        tree.ColumnWidth[3] = 500
        local requestedBatchSize = math.floor(tonumber(batchSize) or 0)
        UI._treePopulation = {
            tree = tree,
            nextIndex = 1,
            batchSize = requestedBatchSize > 0 and requestedBatchSize
                or (#UI._subtitle_blocks_state > 500 and 150 or math.max(1, #UI._subtitle_blocks_state)),
            timecodeContext = App.Resolve:getTimecodeContext(UI.subtitleStartFrame)
        }
        UI._populateTreeBatch()
    end

    function UI.loadActiveTimelineSubtitles()
        local result, loadError = App.Resolve:readActiveSubtitleTrack()
        if not result then
            return false, loadError
        end
        UI.subtitleStartFrame = tonumber(result.subtitleStartFrame)
        UI.blocks = type(result.blocks) == "table" and result.blocks or {}
        UI._subtitle_blocks_state = UI.blocks
        UI.selectedIndex = nil
        UI.suppressEditor = true
        if UI.items and UI.items.SubtitleEditor then
            UI.items.SubtitleEditor.PlainText = ""
        end
        UI.suppressEditor = false
        UI.refreshTree(150)
        return true
    end

    function UI.loadResult(result)
        local job = Core.job
        UI.subtitleStartFrame = job and job.renderState
            and tonumber(job.renderState.subtitleStartFrame or job.renderState.startFrame)
            or nil
        UI.blocks = type(result.blocks) == "table" and result.blocks or {}
        UI._subtitle_blocks_state = UI.blocks
        UI.selectedIndex = nil
        UI.refreshTree()
        local imported, importError = App.Resolve:importSrt(
            tostring(result.srt_path or ""),
            UI.subtitleStartFrame
        )
        if not imported then
            Utils.logError("SRT_IMPORT_FAILED", importError)
            UI.finishJob("srt_import_failed", tostring(importError))
            return false
        end
        local metrics = type(result.metrics) == "table" and result.metrics or {}
        local uncoveredCount = math.max(
            0,
            math.floor(tonumber(metrics.remaining_coverage_hole_count) or 0)
        )
        if uncoveredCount > 0 then
            UI.finishJob("subtitles_created_partial", uncoveredCount)
        else
            UI.finishJob("subtitles_created")
        end
        return true
    end

    function UI.updateRuntimeTaskStatus(status, isDownload)
        local progress = math.max(0, math.min(100, tonumber(status.progress) or 0))
        if isDownload then
            local downloadStatus = tostring(status.download_status or "")
            if downloadStatus == "connecting" then
                local source = UI.downloadSourceName(status.download_source)
                if source ~= "" then
                    UI.setStatusKey("download_connecting", source)
                else
                    UI.setStatusKey("download_preparing")
                end
            elseif downloadStatus == "downloading" or downloadStatus == "fallback" then
                UI.setStatusKey("download_active")
            elseif downloadStatus == "verifying" then
                UI.setStatusKey("download_verifying")
            end
            return
        end
        UI.updateRuntimeSubtitleProgress(progress)
    end

    function UI.pollRuntimeJob()
        local job = Core.job
        if not job then
            return
        end
        local status, statusError = Utils.readJson(job.files.status)
        if type(status) ~= "table" then
            if Utils.fileExists(job.files.status) and not job.statusReadFailureReported then
                job.statusReadFailureReported = true
                Utils.logError(
                    "RUNTIME_STATUS_UNREADABLE",
                    "action=" .. tostring(job.action) .. "; " .. tostring(statusError)
                )
            end
            return
        end
        job.statusReadFailureReported = false
        local state = tostring(status.state or "")
        if not Config.ALLOWED_STATES[state] then
            Utils.logError("RUNTIME_INVALID_STATE", "action=" .. tostring(job.action) .. "; state=" .. state)
            UI.finishJob("runtime_invalid_state", state)
            return
        end
        local isDownload = job.action == "download_models"
        if isDownload then
            UI.updateDownloadButton(status)
        end
        UI.updateRuntimeTaskStatus(status, isDownload)
        if state == "done" then
            local result, resultError = Utils.readJson(job.files.result)
            if type(result) ~= "table" then
                Utils.logError("RUNTIME_RESULT_UNREADABLE", "action=" .. tostring(job.action) .. "; " .. tostring(resultError))
                UI.finishJob("result_unreadable", tostring(resultError))
                return
            end
            if job.action == "transcribe" then
                UI.loadResult(result)
            else
                UI.finishJob("model_ready")
            end
            UI.acknowledgeJob(job)
            if job.renderState then
                App.Resolve:discardAudioCache(job.renderState)
            end
        elseif state == "cancelled" then
            if job.renderState then
                App.Resolve:discardAudioCache(job.renderState)
            end
            UI.finishJob("task_cancelled")
        elseif state == "error" then
            local detail = tostring(status.error or status.message or "Unknown error")
            Utils.logError(isDownload and "MODEL_DOWNLOAD_FAILED" or "RUNTIME_JOB_FAILED", detail)
            if job.renderState then
                App.Resolve:discardAudioCache(job.renderState)
            end
            UI.finishJob(isDownload and "model_download_failed" or "runtime_error", detail)
        end
    end

    function UI.onTimer()
        UI.tick = UI.tick + 1
        if UI._startupPhase == "runtime" then
            UI._startupPhase = "update_wait"
            UI._startupUpdateTick = UI.tick + 10
            UI.ensureRuntime()
        elseif UI._startupPhase == "update_wait" and UI.tick >= UI._startupUpdateTick then
            UI._startupPhase = "ready"
            UI.checkForUpdates()
        end
        if UI._updateCheckPending and UI.tick % 2 == 0 then
            UI.pollUpdateCheck()
        end
        if UI._findDebounceTicks > 0 then
            UI._findDebounceTicks = UI._findDebounceTicks - 1
            if UI._findDebounceTicks == 0 then
                UI._refresh_find_matches()
            end
        end
        if UI._treePopulation then
            UI._populateTreeBatch()
        end
        if UI._findHighlightPopulation then
            UI._populateFindHighlightBatch()
        end
        if UI._startupSubtitleLoadPending then
            UI._startupSubtitleLoadPending = false
            local loadCallOk, loaded, loadError = pcall(UI.loadActiveTimelineSubtitles)
            if not loadCallOk then
                Utils.logError("STARTUP_SUBTITLE_LOAD_FAILED", loaded)
            elseif not loaded and loadError then
                Utils.logError("STARTUP_SUBTITLE_LOAD_FAILED", loadError)
            end
        end
        local job = Core.job
        if job and job.phase == "rendering" and UI.tick % 3 == 0 then
            local pollOk, done, progress, audioPath, renderError = pcall(function()
                return App.Resolve:pollAudioRender()
            end)
            if not pollOk then
                pcall(function()
                    App.Resolve:cancelAudioRender()
                end)
                App.Resolve:discardAudioCache(job.renderState)
                App.Resolve:returnToEditPage()
                Utils.logError("RENDER_POLL_CRASHED", done)
                UI.finishJob("render_failed", tostring(done))
                return
            end
            UI.updateRenderSubtitleProgress(progress)
            if done then
                App.Resolve:returnToEditPage()
                if renderError then
                    App.Resolve:discardAudioCache(job.renderState)
                    Utils.logError("RENDER_FAILED", renderError)
                    UI.finishJob("render_failed", tostring(renderError))
                else
                    UI.submitRenderedAudio(audioPath)
                end
            end
        elseif job and job.phase == "waiting" and UI.tick % 2 == 0 then
            UI.pollRuntimeJob()
        end
        local runtimePollTicks = UI._runtimeLaunchStartedAt and 2 or 10
        if UI.tick % runtimePollTicks == 0 and not Core.job then
            UI.refreshRuntimeStatus()
        end
    end

    function UI._is_find_highlight_color(color)
        if type(color) ~= "table" then
            return false
        end
        for _, component in ipairs({ "R", "G", "B" }) do
            local actual = tonumber(color[component] or 0) or 0
            local expected = tonumber(FIND_HIGHLIGHT_COLOR[component] or 0) or 0
            if math.abs(actual - expected) > 0.000001 then
                return false
            end
        end
        return true
    end

    function UI._count_occurrences(haystack, needle)
        haystack = tostring(haystack or "")
        needle = tostring(needle or "")
        if haystack == "" or needle == "" then
            return 0
        end
        local cursor = 1
        local total = 0
        while true do
            local first = haystack:find(needle, cursor, true)
            if not first then
                return total
            end
            total = total + 1
            cursor = first + 1
        end
    end

    function UI._current_selection_index()
        local index = tonumber(UI.selectedIndex)
        if index and UI._subtitle_blocks_state[index] then
            return index
        end
        return nil
    end

    function UI.currentTreeIndex()
        return UI._current_selection_index()
    end

    function UI._clear_tree_selection()
        local tree = UI.items and UI.items.SubtitleTree
        if not tree then
            return
        end
        local previous = UI._selectedTreeItem
        if previous then
            pcall(function()
                previous.Selected = false
            end)
        end
        UI._selectedTreeItem = nil
    end

    function UI._select_only_tree_row(entryIndex)
        local tree = UI.items and UI.items.SubtitleTree
        if not tree or not entryIndex then
            return
        end
        local targetRow = math.max(0, tonumber(entryIndex) - 1)
        local itemOk, item = pcall(function()
            return tree:TopLevelItem(targetRow)
        end)
        if itemOk and item then
            if UI._selectedTreeItem and UI._selectedTreeItem ~= item then
                pcall(function()
                    UI._selectedTreeItem.Selected = false
                end)
            end
            pcall(function()
                item.Selected = true
            end)
            UI._selectedTreeItem = item
        end
    end

    local function setTreeItemBackground(item, color)
        if not item then
            return
        end
        pcall(function()
            item.BackgroundColor[3] = color
        end)
    end

    function UI._clear_current_highlight(preserveIfStillMatch)
        local index = UI._current_match_highlight
        if not index then
            return
        end
        UI._current_match_highlight = nil
        local tree = UI.items and UI.items.SubtitleTree
        if not tree then
            return
        end
        local itemOk, item = pcall(function()
            return tree:TopLevelItem(index - 1)
        end)
        if not itemOk or not item then
            return
        end
        local text = tostring(item.Text[3] or "")
        if preserveIfStillMatch and UI._find_query ~= ""
            and text:find(UI._find_query, 1, true) then
            setTreeItemBackground(item, FIND_HIGHLIGHT_COLOR)
            UI._findHighlightedRows[index] = true
            UI._current_match_highlight = index
            return
        end
        if UI._sticky_highlights[index] then
            setTreeItemBackground(item, FIND_HIGHLIGHT_COLOR)
            UI._findHighlightedRows[index] = true
        else
            setTreeItemBackground(item, TRANSPARENT_COLOR)
            UI._findHighlightedRows[index] = nil
        end
    end

    function UI._clear_all_find_highlights(force)
        local tree = UI.items and UI.items.SubtitleTree
        if tree then
            local rowsToClear = {}
            for rowIndex in pairs(UI._findHighlightedRows) do
                rowsToClear[rowIndex] = true
            end
            if force then
                for rowIndex in pairs(UI._sticky_highlights) do
                    rowsToClear[rowIndex] = true
                end
            end
            for rowIndex in pairs(rowsToClear) do
                if force or not UI._sticky_highlights[rowIndex] then
                    local itemOk, item = pcall(function()
                        return tree:TopLevelItem(rowIndex - 1)
                    end)
                    if itemOk and item then
                        setTreeItemBackground(item, TRANSPARENT_COLOR)
                    end
                end
            end
        end
        UI._findHighlightPopulation = nil
        UI._findHighlightedRows = {}
        if force then
            UI._sticky_highlights = {}
        end
        UI._current_match_highlight = nil
    end

    function UI._populateFindHighlightBatch()
        local state = UI._findHighlightPopulation
        local tree = UI.items and UI.items.SubtitleTree
        if not state or not tree then
            UI._findHighlightPopulation = nil
            return true
        end
        local first, last = UI._treeBatchBounds(
            #state.rows,
            state.nextIndex,
            state.batchSize
        )
        for position = first, last do
            local rowIndex = state.rows[position]
            local itemOk, item = pcall(function()
                return tree:TopLevelItem(rowIndex - 1)
            end)
            if itemOk and item then
                setTreeItemBackground(item, FIND_HIGHLIGHT_COLOR)
                UI._findHighlightedRows[rowIndex] = true
            end
        end
        state.nextIndex = last + 1
        if state.nextIndex > #state.rows then
            UI._findHighlightPopulation = nil
            return true
        end
        return false
    end

    function UI._reset_find_state(clearQuery)
        UI._clear_all_find_highlights(true)
        UI._find_matches = {}
        UI._find_index = 0
        UI._find_rows = 0
        UI._find_occurrences = 0
        if clearQuery then
            UI._find_query = ""
        else
            UI._find_query = UI.items and UI.items.FindInput and (UI.items.FindInput.Text or "") or UI._find_query
        end
        UI._sticky_highlights = {}
        UI._current_match_highlight = nil
        UI.updateFindStatus(nil)
    end

    function UI._apply_tree_item_logic(item, doTimeline)
        if not item then
            return false
        end
        local index = tonumber(item.Text[0])
        if not index or not UI._subtitle_blocks_state[index] then
            return false
        end
        UI.selectedIndex = index
        UI.suppressEditor = true
        UI.items.SubtitleEditor.PlainText = tostring(item.Text[3] or "")
        UI.suppressEditor = false
        if doTimeline ~= false then
            App.Resolve:jumpToSeconds(
                tonumber(UI._subtitle_blocks_state[index].start) or 0,
                UI.subtitleStartFrame
            )
        end
        return true
    end

    function UI._jump_to_tree_row(entryIndex, doTimeline, ensureVisible)
        local tree = UI.items and UI.items.SubtitleTree
        if not tree or not entryIndex then
            return false
        end
        local itemOk, item = pcall(function()
            return tree:TopLevelItem(entryIndex - 1)
        end)
        if not itemOk or not item then
            return false
        end
        if ensureVisible ~= false then
            UI._suppress_tree_event = true
            UI._clear_tree_selection()
            UI._select_only_tree_row(entryIndex)
            UI._suppress_tree_event = false
        else
            UI._select_only_tree_row(entryIndex)
        end
        pcall(function()
            tree:ScrollToItem(item)
        end)
        return UI._apply_tree_item_logic(item, doTimeline ~= false)
    end

    function UI._refresh_find_matches()
        local findWidget = UI.items and UI.items.FindInput
        local tree = UI.items and UI.items.SubtitleTree
        if not findWidget or not tree then
            return false
        end
        local query = findWidget.Text or ""
        UI._find_query = query
        UI._find_matches = {}
        UI._find_index = 0
        UI._find_rows = 0
        UI._find_occurrences = 0
        UI._clear_all_find_highlights(false)
        if query == "" then
            UI.updateFindStatus("enter_find_text")
            return false
        end
        local totalOccurrences = 0
        for index, block in ipairs(UI._subtitle_blocks_state) do
            local occurrences = UI._count_occurrences(block.text or "", query)
            if occurrences > 0 then
                UI._find_matches[#UI._find_matches + 1] = index
                totalOccurrences = totalOccurrences + occurrences
            end
        end
        if #UI._find_matches == 0 then
            UI.updateFindStatus("no_find_results")
            return false
        end
        UI._findHighlightPopulation = {
            rows = UI._find_matches,
            nextIndex = 1,
            batchSize = #UI._find_matches > 500 and 150 or #UI._find_matches
        }
        UI._populateFindHighlightBatch()
        UI._find_index = 1
        UI._find_rows = #UI._find_matches
        UI._find_occurrences = totalOccurrences
        UI.updateFindStatus("matches_rows_occ", UI._find_rows, totalOccurrences)
        return true
    end

    function UI._ensure_find_matches()
        local findWidget = UI.items and UI.items.FindInput
        if not findWidget then
            return false
        end
        local query = findWidget.Text or ""
        if query == "" then
            UI.updateFindStatus("enter_find_text")
            return false
        end
        if query ~= UI._find_query or #UI._find_matches == 0 then
            return UI._refresh_find_matches()
        end
        return true
    end

    function UI._goto_next_match()
        if not UI._ensure_find_matches() then
            return nil
        end
        if #UI._find_matches == 0 then
            UI.updateFindStatus("no_find_results")
            return nil
        end
        local position = UI._find_index > 0 and UI._find_index or 1
        if position > #UI._find_matches then
            position = 1
        end
        local entryIndex = UI._find_matches[position]
        UI._find_index = position < #UI._find_matches and position + 1 or 1
        UI._clear_current_highlight(true)
        if UI._jump_to_tree_row(entryIndex, true, true) then
            UI._current_match_highlight = entryIndex
            local item = UI.items.SubtitleTree:TopLevelItem(entryIndex - 1)
            setTreeItemBackground(item, FIND_HIGHLIGHT_COLOR)
            UI._findHighlightedRows[entryIndex] = true
            UI.updateFindStatus("match_progress", position, #UI._find_matches)
            return entryIndex
        end
        return nil
    end

    local function replaceAll(text, find, replacement)
        local result = {}
        local cursor = 1
        local count = 0
        while true do
            local first, last = text:find(find, cursor, true)
            if not first then
                result[#result + 1] = text:sub(cursor)
                break
            end
            result[#result + 1] = text:sub(cursor, first - 1)
            result[#result + 1] = replacement
            count = count + 1
            cursor = last + 1
        end
        return table.concat(result), count
    end

    function UI._apply_replace_all()
        local findText = UI.items.FindInput.Text or ""
        if findText == "" then
            UI.updateFindStatus("replace_no_find")
            return
        end
        local replaceText = UI.items.ReplaceInput.Text or ""
        local totalReplaced = 0
        local tree = UI.items.SubtitleTree
        for index, block in ipairs(UI._subtitle_blocks_state) do
            local newText, count = replaceAll(tostring(block.text or ""), findText, replaceText)
            if count > 0 then
                block.text = newText
                totalReplaced = totalReplaced + count
                local item = tree:TopLevelItem(index - 1)
                if item then
                    item.Text[3] = newText
                    setTreeItemBackground(item, FIND_HIGHLIGHT_COLOR)
                end
                if index == UI.selectedIndex then
                    UI.suppressEditor = true
                    UI.items.SubtitleEditor.PlainText = newText
                    UI.suppressEditor = false
                end
                UI._sticky_highlights[index] = true
            end
        end
        if totalReplaced == 0 then
            UI.updateFindStatus("no_replace")
            return
        end
        UI._current_match_highlight = nil
        UI._find_matches = {}
        UI._find_index = 0
        UI._refresh_find_matches()
        UI.updateFindStatus("replace_done", totalReplaced)
    end

    function UI._replace_single()
        local findText = UI.items.FindInput.Text or ""
        if findText == "" then
            UI.updateFindStatus("replace_no_find")
            return
        end
        if not UI._ensure_find_matches() then
            return
        end
        local function currentContains()
            local index = UI._current_selection_index()
            return index and tostring(UI._subtitle_blocks_state[index].text or ""):find(findText, 1, true) ~= nil
        end
        local attempts = 0
        local maximumAttempts = #UI._find_matches
        while not currentContains() and attempts < maximumAttempts do
            if not UI._goto_next_match() then
                break
            end
            attempts = attempts + 1
        end
        if not currentContains() then
            UI.updateFindStatus("no_replace")
            return
        end
        local index = UI._current_selection_index()
        if not index then
            UI.updateFindStatus("no_replace")
            return
        end
        local newText, count = replaceAll(
            tostring(UI._subtitle_blocks_state[index].text or ""),
            findText,
            UI.items.ReplaceInput.Text or ""
        )
        if count == 0 then
            UI.updateFindStatus("no_replace")
            return
        end
        UI._subtitle_blocks_state[index].text = newText
        local item = UI.items.SubtitleTree:TopLevelItem(index - 1)
        if item then
            item.Text[3] = newText
            setTreeItemBackground(item, FIND_HIGHLIGHT_COLOR)
        end
        UI.suppressEditor = true
        UI.items.SubtitleEditor.PlainText = newText
        UI.suppressEditor = false
        UI._sticky_highlights[index] = true
        UI._current_match_highlight = nil
        UI._find_matches = {}
        UI._find_index = 0
        if UI._refresh_find_matches() then
            local nextPosition = 1
            for position, entryIndex in ipairs(UI._find_matches) do
                if entryIndex > index then
                    nextPosition = position
                    break
                end
            end
            UI._find_index = nextPosition
        else
            UI.updateFindStatus("match_progress", 0, 0)
        end
    end

    function UI._on_find_input_text_changed()
        UI._find_matches = {}
        UI._find_index = 0
        UI._find_rows = 0
        UI._find_occurrences = 0
        UI._findDebounceTicks = 2
        UI.updateFindStatus(nil)
    end

    function UI._on_find_input_editing_finished()
        UI._findDebounceTicks = 0
        UI._refresh_find_matches()
    end

    function UI._on_find_button_clicked()
        UI._goto_next_match()
    end

    function UI._on_all_replace_clicked()
        UI._apply_replace_all()
    end

    function UI._on_single_replace_clicked()
        UI._replace_single()
    end

    function UI.selectTreeItem()
        if UI._suppress_tree_event then
            return
        end
        local tree = UI.items.SubtitleTree
        local item = tree:CurrentItem()
        if not item then
            return
        end
        local entryIndex = tonumber(item.Text[0])
        UI._suppress_tree_event = true
        UI._clear_tree_selection()
        if entryIndex then
            UI._select_only_tree_row(entryIndex)
        else
            item.Selected = true
        end
        UI._suppress_tree_event = false
        UI._apply_tree_item_logic(item, true)
    end

    function UI._on_subtitle_editor_text_changed()
        if UI.suppressEditor then
            return
        end
        local index = UI._current_selection_index()
        if not index or not UI._subtitle_blocks_state[index] then
            return
        end
        local newText = UI.items.SubtitleEditor.PlainText or ""
        UI._subtitle_blocks_state[index].text = newText
        local item = UI.items.SubtitleTree:TopLevelItem(index - 1)
        if item then
            item.Text[3] = newText
        end
    end

    UI.findNext = UI._goto_next_match
    UI.replaceSelected = UI._replace_single
    UI.replaceEverywhere = UI._apply_replace_all

    function UI.writeEditedSrt()
        if #UI.blocks == 0 then
            return nil, "No subtitle data is available."
        end
        local jobId = Utils.generateJobId("edited")
        local directory = Utils.joinPath(Config.TEMP_DIR, jobId)
        Utils.ensureDir(directory)
        local path = Utils.joinPath(directory, jobId .. ".srt")
        local parts = {}
        for index, block in ipairs(UI.blocks) do
            parts[#parts + 1] = tostring(index)
                .. "\n" .. Utils.formatSrtTimestamp(block.start)
                .. " --> " .. Utils.formatSrtTimestamp(block["end"])
                .. "\n" .. tostring(block.text or "") .. "\n"
        end
        local written, writeError = Utils.atomicWriteText(path, table.concat(parts, "\n"))
        if not written then
            return nil, writeError
        end
        return path
    end

    function UI.updateSubtitles()
        local path, writeError = UI.writeEditedSrt()
        if not path then
            Utils.logError("EDITED_SRT_WRITE_FAILED", writeError)
            UI.setStatusKey("edited_srt_write_failed", tostring(writeError))
            return
        end
        local imported, importError = App.Resolve:importSrt(path, UI.subtitleStartFrame)
        if imported then
            UI.setStatusKey("edited_subtitles_imported")
        else
            Utils.logError("EDITED_SRT_IMPORT_FAILED", importError)
            UI.setStatusKey("edited_subtitles_import_failed", tostring(importError))
        end
    end

    function UI.buildDiagnosticsText()
        local runtime = Core.runtimeStatus or Utils.runtimeStatus() or {}
        local value = runtime.diagnostics or {}
        local performance = type(value.performance) == "table" and value.performance or {}
        local modelInstalled = value.asr_model == "Ready" and value.aligner_model == "Ready"
        local modelState = modelInstalled and UI.text("Installed") or UI.text("NotInstalled")
        local separator = UI.currentLanguage == "cn" and "：" or ": "
        local diagnosticValueKeys = {
            Unknown = "DiagnosticsUnknown",
            Offline = "DiagnosticsOffline",
            None = "DiagnosticsNone",
            Success = "DiagnosticsSuccess",
            ["Models Ready"] = "DiagnosticsModelsReady",
            ["Self Test Success"] = "DiagnosticsSelfTestSuccess",
            Cancelled = "DiagnosticsCancelled",
            Error = "DiagnosticsError",
            ["Runtime Error"] = "DiagnosticsRuntimeError",
            pending = "DiagnosticsPending",
            processors = "DiagnosticsCacheProcessors",
            processors_only = "DiagnosticsCacheProcessors",
            single = "DiagnosticsCacheSingle",
            single_model = "DiagnosticsCacheSingle",
            dual = "DiagnosticsCacheDual",
            dual_models = "DiagnosticsCacheDual"
        }
        local function diagnosticValue(candidate, fallbackKey)
            local rendered = tostring(candidate or "")
            if rendered == "" then
                return UI.text(fallbackKey)
            end
            local translationKey = diagnosticValueKeys[rendered]
            if translationKey then
                return UI.text(translationKey)
            end
            return rendered
        end
        local function metricValue(candidate, unitKey)
            if candidate == nil or candidate == "" or candidate == "-" then
                return UI.text("DiagnosticsUnknown")
            end
            local unit = unitKey and (" " .. UI.text(unitKey)) or ""
            return tostring(candidate) .. unit
        end
        local function line(labelKey, candidate, fallbackKey)
            return UI.text(labelKey) .. separator .. diagnosticValue(candidate, fallbackKey)
        end
        return table.concat({
            line("DiagnosticsVersion", Config.SCRIPT_VERSION, "DiagnosticsUnknown"),
            line("DiagnosticsProtocol", value.protocol or Config.PROTOCOL_VERSION, "DiagnosticsUnknown"),
            line("DiagnosticsRuntime", value.runtime or runtime.runtime_version, "DiagnosticsOffline"),
            "",
            line("DiagnosticsOS", value.os, "DiagnosticsUnknown"),
            line("DiagnosticsArchitecture", value.architecture, "DiagnosticsUnknown"),
            "",
            line("DiagnosticsBackend", value.backend, "DiagnosticsUnknown"),
            line("DiagnosticsGPU", value.gpu, "DiagnosticsUnknown"),
            line("DiagnosticsDtype", value.dtype, "DiagnosticsUnknown"),
            "",
            line("DiagnosticsModel", "Qwen3-ASR-0.6B · " .. modelState, "DiagnosticsUnknown"),
            "",
            line("DiagnosticsLastJob", value.last_job, "DiagnosticsNone"),
            line("DiagnosticsCachePolicy", runtime.engine_cache_policy, "DiagnosticsUnknown"),
            line(
                "DiagnosticsAudioDuration",
                metricValue(performance.audio_duration_seconds, "DiagnosticsSeconds"),
                "DiagnosticsUnknown"
            ),
            line(
                "DiagnosticsTotal",
                metricValue(performance.total_elapsed_ms, "DiagnosticsMilliseconds"),
                "DiagnosticsUnknown"
            ),
            line("DiagnosticsRTF", metricValue(performance.rtf), "DiagnosticsUnknown"),
            line(
                "DiagnosticsPeakRSS",
                metricValue(performance.peak_rss_bytes, "DiagnosticsBytes"),
                "DiagnosticsUnknown"
            )
        }, "\n")
    end

    function UI.refreshDiagnosticsWindowLanguage()
        if not UI.diagnosticsWindow then
            return false
        end
        UI.diagnosticsWindow.WindowTitle = UI.text("DiagnosticsWindowTitle")
        if UI.diagnosticsItems and UI.diagnosticsItems.DiagnosticsText then
            UI.diagnosticsItems.DiagnosticsText.PlainText = UI.buildDiagnosticsText()
        end
        return true
    end

    function UI.copyDiagnostics()
        if not UI.diagnosticsWindow then
            local dialog = Core.dispatcher:AddWindow({
                ID = "DaVinciASRDiagnostics",
                WindowTitle = UI.text("DiagnosticsWindowTitle"),
                Geometry = { 280, 180, 520, 420 }
            }, Core.ui:VGroup{
                Core.ui:TextEdit{ ID = "DiagnosticsText", ReadOnly = false, Weight = 1 }
            })
            dialog.On.DaVinciASRDiagnostics.Close = function()
                dialog:Hide()
            end
            UI.diagnosticsWindow = dialog
            UI.diagnosticsItems = dialog:GetItems()
        end
        UI.refreshDiagnosticsWindowLanguage()
        UI.diagnosticsWindow:Show()
        UI.diagnosticsItems.DiagnosticsText:SelectAll()
        UI.diagnosticsItems.DiagnosticsText:Copy()
        UI.setStatusKey("diagnostics_copied")
    end

    function UI.bindEvents()
        local window = UI.window
        window.On[Config.WINDOW_ID].Close = function()
            local saveCallOk, saveError = pcall(function()
                return App.Settings:save(UI.collectSettings())
            end)
            if not saveCallOk then
                Utils.logError("SETTINGS_SAVE_CRASHED", saveError)
            end
            if Core.job then
                local job = Core.job
                pcall(function()
                    Utils.ensureDir(job.files.directory)
                    Utils.atomicWriteText(job.files.cancel, "cancel\n")
                end)
                if job.phase == "rendering" then
                    pcall(function()
                        App.Resolve:cancelAudioRender()
                    end)
                end
                pcall(function()
                    App.Resolve:discardAudioCache(job.renderState)
                end)
            end
            local runtimeStopped, runtimeStopError = Utils.stopRuntime()
            if not runtimeStopped then
                Utils.logError("RUNTIME_STOP_FAILED", runtimeStopError)
            end
            local temporaryDataCleaned, temporaryDataError = Utils.cleanupTemporaryData()
            if not temporaryDataCleaned then
                Utils.logError("TEMP_CLEANUP_FAILED", temporaryDataError)
            end
            if Core.timer then
                pcall(function()
                    Core.timer:Stop()
                end)
            end
            if UI.downloadSourceWindow then
                pcall(function()
                    UI.downloadSourceWindow:Hide()
                end)
            end
            if UI.diagnosticsWindow then
                pcall(function()
                    UI.diagnosticsWindow:Hide()
                end)
            end
            Core.dispatcher:ExitLoop()
        end
        window.On.CreateSubtitles.Clicked = UI.guard("CREATE_CLICK_FAILED", UI.startCreateSubtitles)
        window.On.DownloadModels.Clicked = UI.guard("DOWNLOAD_CLICK_FAILED", UI.startDownloadModels)
        window.On.Cancel.Clicked = UI.guard("CANCEL_CLICK_FAILED", UI.cancelCurrentJob)
        window.On.SubtitleTree.ItemClicked = UI.guard("TREE_CLICK_FAILED", UI.selectTreeItem)
        window.On.SubtitleEditor.TextChanged = UI.guard("EDITOR_CHANGE_FAILED", UI._on_subtitle_editor_text_changed)
        window.On.FindInput.TextChanged = UI.guard("FIND_CHANGE_FAILED", UI._on_find_input_text_changed)
        window.On.FindInput.EditingFinished = UI.guard("FIND_FINISH_FAILED", UI._on_find_input_editing_finished)
        window.On.FindButton.Clicked = UI.guard("FIND_CLICK_FAILED", UI._on_find_button_clicked)
        window.On.AllReplaceButton.Clicked = UI.guard("REPLACE_ALL_FAILED", UI._on_all_replace_clicked)
        window.On.SingleReplaceButton.Clicked = UI.guard("REPLACE_ONE_FAILED", UI._on_single_replace_clicked)
        window.On.UpdateSubtitles.Clicked = UI.guard("UPDATE_SUBTITLES_CLICK_FAILED", UI.updateSubtitles)
        window.On.RuntimeStatusButton.Clicked = UI.guard("DIAGNOSTICS_CLICK_FAILED", UI.copyDiagnostics)
        window.On.CopyrightButton.Clicked = UI.guard("MORE_FEATURES_CLICK_FAILED", UI.openMoreFeatures)
        window.On.LangEnCheckBox.Clicked = function()
            UI.applyLanguage("en")
            App.Settings:save(UI.collectSettings())
        end
        window.On.LangCnCheckBox.Clicked = function()
            UI.applyLanguage("cn")
            App.Settings:save(UI.collectSettings())
        end
        Core.dispatcher.On.Timeout = function()
            local timerOk, timerError = pcall(UI.onTimer)
            if not timerOk then
                Utils.logError("TIMER_CALLBACK_FAILED", timerError)
                if Core.timer then
                    pcall(function()
                        Core.timer:Stop()
                    end)
                end
            end
        end
    end

    function UI.run()
        UI.window = UI.buildMainWindow()
        UI.items = UI.window:GetItems()
        UI.items.ModelCombo:AddItems({ "Qwen3-ASR-0.6B" })
        UI.items.ModelCombo.CurrentIndex = 0
        UI.items.LanguageCombo:AddItems(Config.LANGUAGE_LABELS.cn)
        UI.items.SubtitleTree:SetHeaderLabels({ "#", "Start", "End", "Subtitle" })
        UI.applySettings(App.Settings:load())
        UI.bindEvents()
        Core.timer = Core.ui:Timer{ ID = "DaVinciASRPollTimer", Interval = 150, SingleShot = false }
        UI._startupPhase = "runtime"
        UI._startupSubtitleLoadPending = true
        UI.setStatusKey("runtime_starting")
        UI.window:Show()
        Core.timer:Start()
        Core.dispatcher:RunLoop()
        UI.window:Hide()
    end

    App.UI = UI
end

-- 7. Run
function App.Run()
    if not (App.Core and App.Core.dispatcher and App.UI) then
        App.Utils.logError("UI_UNAVAILABLE", "Fusion UIManager or UIDispatcher is unavailable.")
        return
    end
    local directories = {
        app_root = App.Config.APP_ROOT,
        settings = App.Config.CONFIG_DIR,
        cache = App.Config.CACHE_DIR,
        temp = App.Config.TEMP_DIR,
        ipc = App.Config.IPC_DIR
    }
    for label, path in pairs(directories) do
        if not App.Utils.ensureDir(path) then
            App.Utils.logError("STARTUP_DIR_FAILED", "directory=" .. label)
        end
    end
    local runOk, runError = pcall(App.UI.run)
    if not runOk then
        App.Utils.logError("UI_RUN_FAILED", runError)
    end
end

App.Run()
