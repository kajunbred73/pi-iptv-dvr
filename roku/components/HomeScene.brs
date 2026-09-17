' ------------------------------------------------------------------ init

sub init()
    m.menu = m.top.findNode("menu")
    m.content = m.top.findNode("content")
    m.grid = m.top.findNode("grid")
    m.heading = m.top.findNode("heading")
    m.detail = m.top.findNode("detail")
    m.empty = m.top.findNode("empty")
    m.hint = m.top.findNode("hint")
    m.status = m.top.findNode("status")
    m.video = m.top.findNode("video")
    m.readyTimer = m.top.findNode("readyTimer")
    m.readyTimer.observeField("fire", "onReadyCheck")
    m.retryTimer = m.top.findNode("retryTimer")
    m.retryTimer.observeField("fire", "onRetry")
    m.playTimer = m.top.findNode("playTimer")
    m.playTimer.observeField("fire", "onPlayTimeout")
    m.statusTimer = m.top.findNode("statusTimer")

    m.tabs = ["Favorites", "Guide", "Search", "Categories", "Sports Teams", "Recordings", "Scheduled", "Settings"]
    m.menu.content = makeList(m.tabs)
    m.menu.observeField("itemFocused", "onMenuFocused")
    m.menu.observeField("itemSelected", "onMenuSelected")
    m.content.observeField("itemFocused", "onContentFocused")
    m.content.observeField("itemSelected", "onContentSelected")
    m.grid.observeField("selected", "onGridSelected")
    m.grid.observeField("favToggle", "onGridFav")
    m.grid.observeField("pageTime", "onGridPage")
    m.grid.observeField("goBack", "onGridBack")
    m.grid.observeField("detail", "onGridDetail")
    m.video.observeField("state", "onVideoState")
    m.statusTimer.observeField("fire", "loadStatus")

    m.items = []          ' data rows backing the content list
    m.mode = "grid"       ' grid | list | categories | recordings | scheduled | settings
    m.gridFilter = ""     ' extra query string for /api/guide (favorites=1, group=..., q=...)
    m.gridTitle = ""
    m.gridFrom = 0        ' 0 = now
    m.lastQuery = ""
    m.tasks = {}
    m.recordingId = -1
    m.playTitle = ""
    m.streamUrl = ""
    m.isLive = false
    m.startPos = 0
    m.guideOverlay = false
    m.focusResults = false
    m.teams = []
    m.pendingTeam = ""
    m.retryCount = 0
    m.retrying = false
    m.readyAttempts = 0
    m.resumeRecordingId = -1
    m.resumeUrl = ""
    m.resumeTitle = ""
    m.resumeLive = false
    m.resumePos = 0

    m.reg = CreateObject("roRegistrySection", "piiptv")
    m.server = ""
    if m.reg.exists("server") then m.server = m.reg.read("server")

    m.menu.setFocus(true)
    if m.server = ""
        promptServer()
    else
        m.statusTimer.control = "start"
        loadStatus()
        showTab(0)
    end if
end sub

' ------------------------------------------------------------------ helpers

function makeList(labels as Object) as Object
    root = CreateObject("roSGNode", "ContentNode")
    for each t in labels
        n = root.createChild("ContentNode")
        n.title = t
    end for
    return root
end function

function pad2(n as Integer) as String
    if n < 10 then return "0" + n.toStr()
    return n.toStr()
end function

function fmtTime(ts as Dynamic) as String
    if ts = invalid then return ""
    d = CreateObject("roDateTime")
    d.fromSeconds(Int(ts))
    d.toLocalTime()
    h = d.getHours()
    suffix = "AM"
    if h >= 12 then suffix = "PM"
    h = h mod 12
    if h = 0 then h = 12
    return h.toStr() + ":" + pad2(d.getMinutes()) + " " + suffix
end function

function fmtDay(ts as Dynamic) as String
    if ts = invalid then return ""
    d = CreateObject("roDateTime")
    d.fromSeconds(Int(ts))
    d.toLocalTime()
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    return days[d.getDayOfWeek()] + " " + d.getMonth().toStr() + "/" + d.getDayOfMonth().toStr()
end function

function fmtSize(b as Dynamic) as String
    if b = invalid then return ""
    if b > 1000000000 then return (Int(b / 10000000) / 100).toStr() + " GB"
    return Int(b / 1000000).toStr() + " MB"
end function

function fmtDuration(t as Dynamic) as String
    if t = invalid then return ""
    t = Int(t)
    h = Int(t / 3600)
    mins = Int((t mod 3600) / 60)
    s = t mod 60
    if h > 0
        return h.toStr() + ":" + pad2(mins) + ":" + pad2(s)
    else
        return mins.toStr() + ":" + pad2(s)
    end if
end function

function txt(v as Dynamic) as String
    if v = invalid then return ""
    if type(v) = "roString" or type(v) = "String" then return v
    return v.toStr()
end function

function readResumePos(recordingId as Dynamic) as Float
    if recordingId = invalid then return 0
    v = m.reg.read("pos_" + recordingId.toStr())
    if v = invalid or v = "" then return 0
    return v.toFloat()
end function

' Percent-encode for a query string (roUrlTransfer is not allowed on the render thread).
function urlEnc(s as String) as String
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    ba = CreateObject("roByteArray")
    ba.fromAsciiString(s)
    out = ""
    for i = 0 to ba.count() - 1
        b = ba[i]
        c = Chr(b)
        if b < 128 and Instr(1, safe, c) > 0
            out = out + c
        else
            hx = StrI(b, 16)
            if Len(hx) < 2 then hx = "0" + hx
            out = out + "%" + UCase(hx)
        end if
    end for
    return out
end function

sub api(path as String, tag as String, method = "GET" as String, body = "" as String)
    t = CreateObject("roSGNode", "ApiTask")
    t.url = m.server + "/api" + path
    t.method = method
    t.body = body
    t.tag = tag
    t.observeField("response", "onApiResponse")
    t.observeField("error", "onApiError")
    m.tasks[tag] = t
    t.control = "run"
end sub

sub showGridPane(show as Boolean)
    m.grid.visible = show
    m.content.visible = not show
    if show then m.empty.visible = false
end sub

sub setRows(labels as Object, items as Object, emptyText = "Nothing here yet" as String)
    showGridPane(false)
    m.items = items
    m.content.content = makeList(labels)
    m.empty.visible = (labels.count() = 0)
    m.empty.text = emptyText
    if labels.count() > 0
        m.content.jumpToItem = 0
        onContentFocused()
    else
        m.detail.text = ""
    end if
end sub

sub toast(msg as String)
    m.detail.text = msg
end sub

' ------------------------------------------------------------------ navigation

sub onMenuFocused()
    showTab(m.menu.itemFocused)
end sub

sub onMenuSelected()
    tabName = m.tabs[m.menu.itemSelected]
    if tabName = "Search"
        promptSearch()
    else
        focusPane()
    end if
end sub

sub focusPane()
    if m.mode = "grid"
        m.grid.setFocus(true)
    else if m.items.count() > 0 or m.mode = "settings"
        m.content.setFocus(true)
    end if
end sub

sub showTab(idx as Integer)
    tabName = m.tabs[idx]
    m.heading.text = tabName
    if tabName = "Favorites"
        openGrid("favorites=1", "Favorites", "Only channels you starred. Press * on any channel to add/remove.")
    else if tabName = "Guide"
        openGrid("", "Guide", "All enabled channels")
    else if tabName = "Search"
        m.mode = "list"
        m.hint.text = "OK: search by channel name"
        if m.lastQuery <> ""
            m.heading.text = "Search: " + m.lastQuery
            api("/channels?q=" + urlEnc(m.lastQuery), "channels")
        else
            setRows([], [], "Press OK to type a channel name (e.g. ABC, ESPN, KATC).")
        end if
    else if tabName = "Categories"
        m.mode = "categories"
        m.hint.text = "OK: open category guide"
        api("/groups", "groups")
    else if tabName = "Sports Teams"
        m.mode = "teams"
        m.hint.text = "OK: find game   *: delete   Back: menu"
        loadTeams()
    else if tabName = "Recordings"
        m.mode = "recordings"
        m.hint.text = "OK: play   *: delete"
        api("/recordings", "recordings")
    else if tabName = "Scheduled"
        m.mode = "scheduled"
        m.hint.text = "OK: cancel recording"
        api("/schedules", "schedules")
    else if tabName = "Settings"
        m.mode = "settings"
        m.hint.text = ""
        showSettings()
    end if
end sub

sub openGrid(filter as String, title as String, subtitle as String)
    m.mode = "grid"
    m.gridFilter = filter
    m.gridTitle = title
    m.gridFrom = 0
    m.heading.text = title
    m.hint.text = "Up/Down: channel   Left/Right: time   OK: watch / record   *: favorite   Back: menu"
    showGridPane(true)
    m.detail.text = subtitle
    loadGrid()
end sub

sub loadGrid()
    q = "/guide?hours=3"
    if m.gridFrom > 0 then q = q + "&from=" + m.gridFrom.toStr()
    if m.gridFilter <> "" then q = q + "&" + m.gridFilter
    api(q, "guide")
end sub

sub showSettings()
    setRows(["Server address: " + m.server, "Refresh playlist and guide on server", "Version 1.1"], ["server", "refresh", "version"])
end sub

sub onContentFocused()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    if m.mode = "list"
        info = txt(it.grp)
        if it.now <> invalid
            info = info + "   |   Now: " + txt(it.now.title) + " (" + fmtTime(it.now.start) + " - " + fmtTime(it.now["stop"]) + ")"
        end if
        if it["next"] <> invalid then info = info + "   |   Next: " + txt(it["next"].title)
        m.detail.text = info
    else if m.mode = "categories"
        m.detail.text = txt(it.count) + " channels"
    else if m.mode = "recordings"
        m.detail.text = txt(it.description)
    else if m.mode = "teams"
        m.detail.text = txt(it.name)
    else if m.mode = "scheduled"
        m.detail.text = fmtDay(it.start) + " " + fmtTime(it.start) + " - " + fmtTime(it["stop"])
    else
        m.detail.text = ""
    end if
end sub

sub onContentSelected()
    i = m.content.itemSelected
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    if m.mode = "list"
        channelMenu(it, it.now)
    else if m.mode = "categories"
        openGrid("group=" + urlEnc(it.name), txt(it.name), txt(it.count) + " channels in this category")
        m.grid.setFocus(true)
    else if m.mode = "teams"
        if it.action = "add" then
            promptTeamAdd()
        else
            findGame(it.name)
        end if
    else if m.mode = "recordings"
        m.recordingId = it.id
        isLive = (it.status = "recording")
        saved = readResumePos(it.id)
        if saved > 5 and not isLive
            showResumeDialog(it.stream_url, it.title, isLive, saved, it.id)
        else
            play(it.stream_url, it.title, isLive)
        end if
    else if m.mode = "scheduled"
        confirm("Cancel recording '" + txt(it.title) + "'?", "cancel", { id: it.id })
    else if m.mode = "settings"
        if it = "server"
            promptServer()
        else if it = "refresh"
            toast("Refreshing on server...")
            api("/refresh", "refresh", "POST", "{}")
        end if
    end if
end sub

sub toggleFavorite(ch as Object)
    fav = not (ch.favorite = 1)
    body = "{""favorite"": false}"
    if fav then body = "{""favorite"": true}"
    api("/channels/" + txt(ch.id) + "/favorite", "fav_ok", "POST", body)
    if fav then toast("Added " + txt(ch.name) + " to Favorites") else toast("Removed " + txt(ch.name) + " from Favorites")
end sub

sub favoriteFocused()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    toggleFavorite(m.items[i])
end sub

sub deleteFocused()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    confirm("Delete recording '" + txt(it.title) + "'?", "delete", { id: it.id })
end sub

' ---- guide grid events

sub onGridSelected()
    sel = m.grid.selected
    if sel = invalid or sel.channel = invalid then return
    if m.guideOverlay then hideGuideOverlay()
    channelMenu(sel.channel, sel.program)
end sub

sub onGridFav()
    ch = m.grid.favToggle
    if ch <> invalid then toggleFavorite(ch)
end sub

sub onGridPage()
    dir = m.grid.pageTime
    d = m.grid.data
    if d = invalid or d.start = invalid then return
    nowT = d.now
    if nowT = invalid then nowT = d.start
    floorNow = nowT - (nowT mod 1800)
    newFrom = d.start + dir * 5400
    if newFrom < floorNow then newFrom = floorNow
    if newFrom = d.start then return
    m.gridFrom = newFrom
    loadGrid()
end sub

sub onGridBack()
    if m.guideOverlay
        hideGuideOverlay()
    else
        m.menu.setFocus(true)
    end if
end sub

sub onGridDetail()
    if m.mode = "grid" then m.detail.text = m.grid.detail
end sub

' Options for a channel (+ optionally the highlighted program)
sub channelMenu(ch as Object, prog as Object)
    nowT = 0
    gd = m.grid.data
    if m.mode = "grid" and gd <> invalid and gd.now <> invalid then nowT = gd.now
    if nowT = 0 then nowT = CreateObject("roDateTime").asSeconds()
    isNow = (prog = invalid) or (prog.start <= nowT and prog["stop"] > nowT)
    d = CreateObject("roSGNode", "Dialog")
    d.title = txt(ch.name)
    buttons = []
    actions = []
    if prog <> invalid
        d.message = fmtTime(prog.start) + " - " + fmtTime(prog["stop"]) + "   " + txt(prog.title)
    else
        d.message = "No guide data for this channel."
    end if
    if isNow
        buttons.push("Watch now")
        actions.push("watch")
    else
        buttons.push("Watch channel now")
        actions.push("watch")
    end if
    if prog <> invalid
        if prog.scheduled = true
            buttons.push("Cancel recording")
            actions.push("unschedule")
        else if isNow
            buttons.push("Record this show")
            actions.push("record")
        else
            buttons.push("Record")
            actions.push("record")
        end if
    else
        buttons.push("Record 60 minutes")
        actions.push("record60")
    end if
    if ch.favorite = 1
        buttons.push("Remove from Favorites")
    else
        buttons.push("Add to Favorites")
    end if
    actions.push("fav")
    buttons.push("Close")
    actions.push("close")
    d.buttons = buttons
    d.addField("actions", "array", false)
    d.addField("channel", "assocarray", false)
    d.addField("program", "assocarray", false)
    d.actions = actions
    d.channel = ch
    if prog <> invalid then d.program = prog
    d.observeField("buttonSelected", "onChannelMenu")
    m.top.dialog = d
end sub

sub onChannelMenu(ev as Object)
    d = ev.getRoSGNode()
    d.close = true
    idx = ev.getData()
    if idx < 0 or idx >= d.actions.count() then return
    action = d.actions[idx]
    ch = d.channel
    prog = d.program
    if action = "watch"
        playChannel(ch)
    else if action = "record"
        api("/schedules", "scheduled_ok", "POST", FormatJson({ channel_id: ch.id, program_start: prog.start }))
    else if action = "record60"
        api("/schedules", "scheduled_ok", "POST", FormatJson({ channel_id: ch.id, minutes: 60 }))
    else if action = "unschedule"
        api("/schedules/by-program?channel_id=" + txt(ch.id) + "&start=" + txt(prog.start), "cancel_ok", "DELETE", "")
    else if action = "fav"
        toggleFavorite(ch)
    end if
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press then return false
    if m.video.visible
        if key = "back"
            if m.top.dialog <> invalid
                if m.top.dialog.loading = true
                    stopVideo()
                else
                    m.top.dialog.close = true
                    m.top.dialog = invalid
                    m.video.setFocus(true)
                end if
                return true
            end if
            stopVideo()
            return true
        else if key = "down"
            if m.top.dialog = invalid and not m.guideOverlay then trickMenu()
            return true
        else if key = "up"
            if m.top.dialog = invalid and not m.guideOverlay
                showGuideOverlay()
                return true
            end if
            return false
        end if
        return false
    end if
    if key = "back"
        if m.top.dialog <> invalid and m.top.dialog.resume = true
            m.top.dialog.close = true
            m.top.dialog = invalid
            focusPane()
            return true
        end if
        if m.content.hasFocus() or m.grid.hasFocus()
            m.menu.setFocus(true)
            return true
        end if
        return false
    end if
    if key = "left" and m.content.hasFocus()
        m.menu.setFocus(true)
        return true
    end if
    if key = "right" and m.menu.hasFocus()
        focusPane()
        return true
    end if
    if key = "options"
        if m.content.hasFocus()
            if m.mode = "list"
                favoriteFocused()
                return true
            else if m.mode = "recordings"
                deleteFocused()
                return true
            else if m.mode = "teams"
                deleteTeamFocused()
                return true
            end if
        else if m.menu.hasFocus() and m.tabs[m.menu.itemFocused] = "Search"
            promptSearch()
            return true
        end if
    end if
    return false
end function

' ------------------------------------------------------------------ dialogs

sub confirm(msg as String, action as String, payload as Object)
    d = CreateObject("roSGNode", "Dialog")
    d.title = "Pi IPTV DVR"
    d.message = msg
    d.buttons = ["Yes", "No"]
    d.addField("action", "string", false)
    d.addField("payload", "assocarray", false)
    d.action = action
    d.payload = payload
    d.observeField("buttonSelected", "onConfirm")
    m.top.dialog = d
end sub

sub onConfirm(ev as Object)
    d = ev.getRoSGNode()
    d.close = true
    if ev.getData() <> 0 then return
    p = d.payload
    if d.action = "schedule"
        api("/schedules", "scheduled_ok", "POST", FormatJson(p))
    else if d.action = "cancel"
        api("/schedules/" + txt(p.id), "cancel_ok", "DELETE", "")
    else if d.action = "delete"
        api("/recordings/" + txt(p.id), "delete_ok", "DELETE", "")
    end if
end sub

sub promptSearch()
    k = CreateObject("roSGNode", "KeyboardDialog")
    k.title = "Search channels"
    k.message = "Type part of a channel name"
    k.text = m.lastQuery
    k.buttons = ["Search", "Cancel"]
    k.observeField("buttonSelected", "onSearchEntered")
    m.top.dialog = k
end sub

sub onSearchEntered(ev as Object)
    k = ev.getRoSGNode()
    if ev.getData() = 0
        q = k.text.trim()
        if q <> ""
            m.lastQuery = q
            m.mode = "list"
            m.heading.text = "Search: " + q
            m.hint.text = "OK: watch / record   *: favorite"
            m.focusResults = true
            api("/channels?q=" + urlEnc(q), "channels")
        end if
    end if
    k.close = true
end sub

sub loadTeams()
    t = m.reg.read("teams")
    if t = invalid
        m.teams = []
    else
        m.teams = ParseJson(t)
        if m.teams = invalid or m.teams.count() = invalid
            m.teams = []
        end if
    end if
    labels = ["Add team"]
    items = [{ name: "Add team", action: "add" }]
    for each name in m.teams
        labels.push(name)
        items.push({ name: name })
    end for
    setRows(labels, items, "No teams yet. Select 'Add team' to create your list.")
end sub

sub promptTeamAdd()
    k = CreateObject("roSGNode", "KeyboardDialog")
    k.title = "Add sports team"
    k.message = "Type the team name (e.g. Cowboys, Celtics)"
    k.text = ""
    k.buttons = ["Add", "Cancel"]
    k.observeField("buttonSelected", "onTeamEntered")
    m.top.dialog = k
end sub

sub onTeamEntered(ev as Object)
    k = ev.getRoSGNode()
    if ev.getData() = 0
        name = k.text.trim()
        if name <> "" then addTeam(name)
    end if
    k.close = true
    m.top.dialog = invalid
end sub

sub addTeam(name as String)
    for each t in m.teams
        if LCase(t) = LCase(name) then return
    end for
    m.teams.push(name)
    m.reg.write("teams", FormatJson(m.teams))
    m.reg.flush()
    loadTeams()
end sub

sub deleteTeamFocused()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    if it.action = "add" then return
    if m.teams.count() > 0
        m.teams.delete(i - 1)
        m.reg.write("teams", FormatJson(m.teams))
        m.reg.flush()
        loadTeams()
    end if
end sub

sub findGame(name as String)
    m.pendingTeam = name
    showLoading("Finding " + name + "...")
    api("/channels?q=" + urlEnc(name), "sports")
end sub

sub promptServer()
    k = CreateObject("roSGNode", "KeyboardDialog")
    k.title = "Raspberry Pi server address"
    k.message = "Enter the IP or hostname shown on the Pi web page, e.g. 192.168.1.50:8080"
    if m.server <> ""
        k.text = m.server.replace("http://", "")
    else
        k.text = "192.168.1."
    end if
    k.buttons = ["Save", "Cancel"]
    k.observeField("buttonSelected", "onServerEntered")
    m.top.dialog = k
end sub

sub onServerEntered(ev as Object)
    k = ev.getRoSGNode()
    if ev.getData() = 0
        addr = k.text.trim()
        if addr <> ""
            if Left(addr, 4) <> "http" then addr = "http://" + addr
            if Instr(1, addr.mid(7), ":") = 0 then addr = addr + ":8080"
            m.server = addr
            m.reg.write("server", addr)
            m.reg.flush()
            m.statusTimer.control = "start"
            loadStatus()
            showTab(m.menu.itemFocused)
        end if
    end if
    k.close = true
end sub

' ------------------------------------------------------------------ playback

sub play(url as String, title as String, isLive as Boolean, startPos = 0)
    m.playTitle = title
    m.streamUrl = url
    m.isLive = isLive
    m.startPos = startPos
    m.retryCount = 0
    m.retrying = false
    m.readyTimer.control = "stop"
    m.retryTimer.control = "stop"
    if m.top.dialog <> invalid and m.top.dialog.loading = true
        ' keep the existing loading dialog
    else
        showLoading("Buffering...")
    end if
    ' Only stop if something is actually playing; calling "stop" on an idle Video can fire
    ' a stray "finished" state that hits the retry path before the new content is loaded.
    if m.video.visible and (m.video.state = "playing" or m.video.state = "paused" or m.video.state = "buffering")
        m.video.control = "stop"
    end if
    c = CreateObject("roSGNode", "ContentNode")
    c.url = url
    c.title = title
    c.streamFormat = "hls"
    c.live = isLive
    m.video.content = c
    m.video.loop = false
    m.video.visible = true
    m.video.setFocus(true)
    m.video.control = "play"
    m.playTimer.control = "start"
end sub

' Something went wrong starting/playing video: stop and tell the user why (a toast is hidden
' behind the loading box, so use a real dialog).
sub playError(msg as String)
    stopVideo()
    d = CreateObject("roSGNode", "Dialog")
    d.title = "Can't play"
    d.message = msg
    d.buttons = ["OK"]
    d.observeField("buttonSelected", "onErrorDialog")
    m.top.dialog = d
    d.setFocus(true)
end sub

sub onErrorDialog(ev as Object)
    d = ev.getRoSGNode()
    d.close = true
    m.top.dialog = invalid
    focusPane()
end sub

sub onPlayTimeout()
    if m.video.visible and m.video.state <> "playing" and m.video.state <> "paused"
        playError("The stream did not start within 30 seconds (player state: " + m.video.state + "). " + Chr(10) + "Check that the Pi service is running: sudo systemctl status pi-iptv-dvr")
    end if
end sub

sub showLoading(msg as String)
    if m.top.dialog <> invalid
        m.top.dialog.close = true
        m.top.dialog = invalid
    end if
    d = CreateObject("roSGNode", "ProgressDialog")
    d.title = msg
    d.message = m.playTitle
    d.addField("loading", "boolean", false)
    d.loading = true
    m.top.dialog = d
    d.setFocus(true)
end sub

sub hideLoading()
    if m.top.dialog <> invalid
        m.top.dialog.close = true
        m.top.dialog = invalid
    end if
end sub

sub showGuideOverlay()
    m.guideOverlay = true
    if m.grid.data = invalid then loadGrid()
    m.video.translation = [960, 0]
    m.video.width = 960
    m.video.height = 1080
    m.grid.visible = true
    m.grid.translation = [20, 120]
    m.grid.scale = [0.55, 0.55]
    m.grid.setFocus(true)
    m.hint.text = "OK: menu   Back: close guide"
end sub

sub hideGuideOverlay()
    m.guideOverlay = false
    m.video.translation = [0, 0]
    m.video.width = 1920
    m.video.height = 1080
    m.grid.visible = false
    m.grid.translation = [470, 170]
    m.grid.scale = [1, 1]
    m.video.setFocus(true)
    m.hint.text = "OK: pause/play/rewind   Back: stop   Down: controls"
end sub

sub showResumeDialog(url as String, title as String, isLive as Boolean, resumeFrom as Float, recordingId as Integer)
    m.resumeUrl = url
    m.resumeTitle = title
    m.resumeLive = isLive
    m.resumePos = resumeFrom
    m.resumeRecordingId = recordingId
    hideLoading()
    d = CreateObject("roSGNode", "Dialog")
    d.title = "Resume viewing?"
    d.message = "Resume from " + fmtDuration(resumeFrom) + " or start over?"
    d.buttons = ["Resume", "Start over"]
    d.addField("resume", "boolean", false)
    d.resume = true
    d.observeField("buttonSelected", "onResumeDialog")
    m.top.dialog = d
    d.setFocus(true)
end sub

sub onResumeDialog(ev as Object)
    d = ev.getRoSGNode()
    d.close = true
    m.top.dialog = invalid
    idx = ev.getData()
    if idx < 0 then return
    startPos = 0
    if idx = 0 then startPos = m.resumePos
    m.recordingId = m.resumeRecordingId
    play(m.resumeUrl, m.resumeTitle, m.resumeLive, startPos)
end sub

sub playChannel(ch as Object)
    m.pendingChannel = ch
    m.recordingId = -1
    m.playTitle = ch.name
    showLoading("Tuning...")
    api("/timeshift", "timeshift", "POST", FormatJson({ channel_id: ch.id }))
end sub

sub trickMenu()
    if m.recordingId < 0 then return
    d = CreateObject("roSGNode", "Dialog")
    d.title = m.playTitle
    d.message = "OK: select   Back: close"
    if m.video.state = "paused"
        buttons = ["Play", "Back 30s", "Forward 30s", "Jump to live", "Keep recording"]
        actions = ["play", "back30", "fwd30", "live", "keep"]
    else
        buttons = ["Pause", "Back 30s", "Forward 30s", "Jump to live", "Keep recording"]
        actions = ["pause", "back30", "fwd30", "live", "keep"]
    end if
    d.buttons = buttons
    d.addField("actions", "array", false)
    d.addField("recordingId", "integer", false)
    d.actions = actions
    d.recordingId = m.recordingId
    d.observeField("buttonSelected", "onTrickMenu")
    m.top.dialog = d
    d.setFocus(true)
end sub

sub onTrickMenu(ev as Object)
    d = ev.getRoSGNode()
    d.close = true
    idx = ev.getData()
    if idx < 0 or idx >= d.actions.count() then return
    action = d.actions[idx]
    if action = "pause"
        m.video.control = "pause"
    else if action = "play"
        m.video.control = "resume"
    else if action = "back30"
        p = m.video.position - 30
        if p < 0 then p = 0
        m.video.seek = p
    else if action = "fwd30"
        m.video.seek = m.video.position + 30
    else if action = "live"
        ' Reloading the live playlist puts the player back at the live edge.
        m.video.control = "play"
    else if action = "keep"
        if d.recordingId > 0
            api("/timeshift/" + d.recordingId.toStr() + "/keep", "keep", "POST", "")
        end if
    end if
    m.video.setFocus(true)
end sub

sub stopVideo(clearResume = false)
    if m.recordingId >= 0
        if clearResume
            m.reg.delete("pos_" + m.recordingId.toStr())
            m.reg.flush()
        else
            lastPos = m.video.position
            totalDur = m.video.duration
            if lastPos > 5 and (totalDur <= 0 or lastPos < totalDur - 15)
                m.reg.write("pos_" + m.recordingId.toStr(), lastPos.toStr())
                m.reg.flush()
            end if
        end if
    end if
    hideLoading()
    m.readyTimer.control = "stop"
    m.playTimer.control = "stop"
    m.video.control = "stop"
    m.video.visible = false
    if m.guideOverlay
        m.guideOverlay = false
        m.video.translation = [0, 0]
        m.video.width = 1920
        m.video.height = 1080
        m.grid.visible = false
        m.grid.translation = [470, 170]
        m.grid.scale = [1, 1]
    end if
    m.recordingId = -1
    m.isLive = false
    m.retrying = false
    m.retryTimer.control = "stop"
    focusPane()
end sub

sub onVideoState()
    st = m.video.state
    if st = "playing"
        hideLoading()
        m.playTimer.control = "stop"
        m.video.setFocus(true)
        if not m.isLive and m.startPos > 0
            m.video.seek = m.startPos
            m.startPos = 0
        end if
        m.retryCount = 0
        m.retrying = false
    else if st = "error"
        if m.recordingId >= 0 and m.isLive and m.retryCount < 5 and not m.retrying
            m.retrying = true
            m.retryCount = m.retryCount + 1
            m.retryTimer.control = "start"
        else
            playError("Playback error: " + txt(m.video.errorMsg) + " (code " + txt(m.video.errorCode) + ")" + Chr(10) + m.streamUrl)
        end if
    else if st = "finished"
        ' A live buffer only really ends when the Pi writes ENDLIST; if the player ran off the
        ' end of the growing playlist, rejoin at the live edge instead of stopping/looping.
        if m.recordingId >= 0 and m.isLive and m.retryCount < 5 and not m.retrying
            m.retrying = true
            m.retryCount = m.retryCount + 1
            m.retryTimer.control = "start"
        else
            if m.isLive
                playError("The live buffer ended before playback started.")
            else
                stopVideo(true)
            end if
        end if
    end if
end sub

sub onRetry()
    m.retrying = false
    if m.video.visible and m.recordingId >= 0
        ' Ask the Pi whether the buffer is still being written before rejoining.
        m.readyAttempts = 0
        api("/timeshift/" + m.recordingId.toStr() + "/ready", "rejoin")
    end if
end sub

sub onReadyCheck()
    if m.recordingId >= 0
        api("/timeshift/" + m.recordingId.toStr() + "/ready", "readycheck")
    end if
end sub

' ------------------------------------------------------------------ API responses

sub loadStatus()
    if m.server = "" then return
    api("/status", "status")
    ' Heartbeat so the Pi keeps the live buffer running while we watch (or sit paused).
    if m.video.visible and m.isLive and m.recordingId >= 0
        api("/timeshift/" + m.recordingId.toStr() + "/touch", "touch", "POST", "")
    end if
end sub

sub onApiError(ev as Object)
    t = ev.getRoSGNode()
    if t.tag = "touch" then return
    if t.tag = "timeshift" or t.tag = "readycheck" or t.tag = "rejoin"
        m.readyTimer.control = "stop"
        playError("The Pi did not answer " + t.url + Chr(10) + t.error + Chr(10) + "If this says HTTP 404, the Pi is running old server code: cd pi-iptv-dvr && git pull && sudo systemctl restart pi-iptv-dvr")
        return
    end if
    if t.tag = "status"
        m.status.text = "Cannot reach " + m.server
    else
        toast("Error: " + t.error)
        if t.tag = "channels" or t.tag = "groups" or t.tag = "recordings" or t.tag = "schedules"
            setRows([], [], "Cannot reach the Pi at " + m.server + ". Check Settings.")
        end if
    end if
end sub

sub onApiResponse(ev as Object)
    r = ev.getData()
    tag = r.tag
    if tag = "status"
        m.status.text = txt(r.channels) + " channels  |  " + txt(r.recordings) + " recordings  |  " + m.server
    else if tag = "timeshift"
        if r.ok = true or r.ok = 1
            m.recordingId = r.recording_id
            m.streamUrl = r.stream_url
            m.playTitle = txt(m.pendingChannel.name)
            m.readyAttempts = 0
            m.readyTimer.control = "start"
        else
            playError("Could not start the live buffer: " + txt(r.error))
        end if
    else if tag = "readycheck"
        if r.ready = true or r.ready = 1
            m.readyTimer.control = "stop"
            play(m.streamUrl, m.playTitle, true)
        else if txt(r.status) <> "recording"
            m.readyTimer.control = "stop"
            playError("The Pi could not open this channel's stream. ffmpeg said:" + Chr(10) + txt(r.error))
        else
            m.readyAttempts = m.readyAttempts + 1
            if m.top.dialog <> invalid and m.top.dialog.loading = true
                m.top.dialog.title = "Buffering live TV... " + txt(r.segments) + "/3"
            end if
            if m.readyAttempts > 60
                m.readyTimer.control = "stop"
                playError("The Pi is still not producing video after 60 s (" + txt(r.segments) + " segments). The provider stream may be down or too slow.")
            end if
        end if
    else if tag = "rejoin"
        if not m.video.visible then return
        if txt(r.status) = "recording" or (r.ready = true)
            m.video.control = "play"
        else
            playError("The Pi stopped this channel's recording (" + txt(r.status) + "). " + txt(r.error))
        end if
    else if tag = "touch"
        ' heartbeat; nothing to do
    else if tag = "keep"
        if r.ok = true or r.ok = 1
            toast("Recording saved")
        else
            toast("Could not save recording")
        end if
    else if tag = "guide"
        if m.mode <> "grid" and not m.guideOverlay then return
        m.grid.data = r
        if m.guideOverlay then m.grid.setFocus(true)
        n = 0
        if r.channels <> invalid then n = r.channels.count()
        if n = 0 and m.gridFilter = "favorites=1"
            m.detail.text = "No favorites yet. Open Guide, Categories or Search, highlight a channel and press * to star it."
        end if
    else if tag = "channels"
        if m.mode <> "list" then return
        labels = []
        for each ch in r.items
            star = "     "
            if ch.favorite = 1 then star = "*   "
            line = star + txt(ch.num) + "   " + txt(ch.name)
            if ch.now <> invalid then line = line + "   -   " + txt(ch.now.title)
            labels.push(line)
        end for
        setRows(labels, r.items, "No channels match '" + m.lastQuery + "'. Only channels in enabled groups are searched (Pi Settings > Channel groups).")
        m.hint.text = "OK: watch / record / favorite   *: star   Left: menu"
        if m.focusResults and labels.count() > 0 and m.top.dialog = invalid then m.content.setFocus(true)
        m.focusResults = false
    else if tag = "groups"
        if m.mode <> "categories" then return
        labels = []
        items = []
        for each g in r.items
            if g.enabled = 1
                name = txt(g.name)
                if name = "" then name = "(no group)"
                labels.push(name + "   (" + txt(g.count) + ")")
                items.push(g)
            end if
        end for
        setRows(labels, items, "No groups enabled. On the Pi web page go to Settings > Channel groups and tick the groups you watch.")
    else if tag = "recordings"
        if m.mode <> "recordings" then return
        labels = []
        for each rec in r.items
            tagTxt = ""
            if rec.status = "recording" then tagTxt = "  [RECORDING]"
            if rec.status = "failed" then tagTxt = "  [FAILED]"
            labels.push(fmtDay(rec.start) + " " + fmtTime(rec.start) + "   " + txt(rec.title) + "   (" + fmtDuration(rec.duration) + ", " + txt(rec.channel_name) + ", " + fmtSize(rec.size_bytes) + ")" + tagTxt)
        end for
        setRows(labels, r.items, "No recordings yet. Pick a show in the Guide and choose Record.")
    else if tag = "schedules"
        if m.mode <> "scheduled" then return
        labels = []
        for each s in r.items
            tagTxt = ""
            if s.status = "recording" then tagTxt = "  [RECORDING]"
            labels.push(fmtDay(s.start) + " " + fmtTime(s.start) + "   " + txt(s.title) + "   (" + txt(s.channel_name) + ")" + tagTxt)
        end for
        setRows(labels, r.items, "Nothing scheduled.")
    else if tag = "scheduled_ok"
        toast("Recording scheduled")
        if m.mode = "grid" then loadGrid()
    else if tag = "cancel_ok"
        toast("Recording cancelled")
        if m.mode = "grid" then loadGrid()
        if m.mode = "scheduled" then api("/schedules", "schedules")
    else if tag = "fav_ok"
        if m.mode = "grid" then loadGrid()
        if m.mode = "list" then api("/channels?q=" + urlEnc(m.lastQuery), "channels")
    else if tag = "delete_ok"
        toast("Deleted")
        api("/recordings", "recordings")
    else if tag = "sports"
        hideLoading()
        if r.items = invalid or r.items.count() = 0
            toast("No live game found for " + txt(m.pendingTeam))
        else
            playChannel(r.items[0])
        end if
    else if tag = "refresh"
        if r.started = true then toast("Import started on server; it may take a few minutes") else toast("Import already running")
        loadStatus()
    end if
end sub
