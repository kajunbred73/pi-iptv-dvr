' ------------------------------------------------------------------ init

sub init()
    m.menu = m.top.findNode("menu")
    m.content = m.top.findNode("content")
    m.heading = m.top.findNode("heading")
    m.detail = m.top.findNode("detail")
    m.empty = m.top.findNode("empty")
    m.hint = m.top.findNode("hint")
    m.status = m.top.findNode("status")
    m.video = m.top.findNode("video")
    m.statusTimer = m.top.findNode("statusTimer")

    m.tabs = ["Live TV", "Guide", "Recordings", "Scheduled", "Settings"]
    m.menu.content = makeList(m.tabs)
    m.menu.observeField("itemFocused", "onMenuFocused")
    m.menu.observeField("itemSelected", "onMenuSelected")
    m.content.observeField("itemFocused", "onContentFocused")
    m.content.observeField("itemSelected", "onContentSelected")
    m.video.observeField("state", "onVideoState")
    m.statusTimer.observeField("fire", "loadStatus")

    m.items = []          ' data rows backing the content list
    m.mode = "live"       ' live | guide | programs | recordings | scheduled | settings
    m.guideChannel = invalid
    m.tasks = {}

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

function txt(v as Dynamic) as String
    if v = invalid then return ""
    if type(v) = "roString" or type(v) = "String" then return v
    return v.toStr()
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

sub setRows(labels as Object, items as Object, emptyText = "Nothing here yet" as String)
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
    if m.mode = "settings"
        m.content.setFocus(true)
    else if m.items.count() > 0
        m.content.setFocus(true)
    end if
end sub

sub showTab(idx as Integer)
    tabName = m.tabs[idx]
    m.heading.text = tabName
    if tabName = "Live TV"
        m.mode = "live"
        m.hint.text = "OK: watch   *: record"
        api("/channels", "channels")
    else if tabName = "Guide"
        m.mode = "guide"
        m.hint.text = "OK: open channel guide"
        api("/channels", "guidechannels")
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

sub showSettings()
    setRows(["Server address: " + m.server, "Refresh playlist and guide on server", "Version 1.0"], ["server", "refresh", "version"])
end sub

sub onContentFocused()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    if m.mode = "live" or m.mode = "guide"
        info = txt(it.grp)
        if it.now <> invalid
            info = info + "   |   Now: " + txt(it.now.title) + " (" + fmtTime(it.now.start) + " - " + fmtTime(it.now["stop"]) + ")"
        end if
        if it["next"] <> invalid then info = info + "   |   Next: " + txt(it["next"].title)
        m.detail.text = info
    else if m.mode = "programs"
        m.detail.text = txt(it.description)
    else if m.mode = "recordings"
        m.detail.text = txt(it.description)
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
    if m.mode = "live"
        play(it.stream_url, it.name, true)
    else if m.mode = "guide"
        m.guideChannel = it
        m.heading.text = "Guide: " + txt(it.name)
        m.hint.text = "OK: record   Back: channels"
        m.mode = "programs"
        api("/epg/" + txt(it.id) + "?hours=48", "programs")
    else if m.mode = "programs"
        if it.scheduled = true
            toast("Already scheduled")
        else
            confirm("Record '" + txt(it.title) + "'?", "schedule", { channel_id: m.guideChannel.id, program_start: it.start })
        end if
    else if m.mode = "recordings"
        play(it.stream_url, it.title, false)
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

sub recordFocused()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    if it.now <> invalid
        confirm("Record '" + txt(it.now.title) + "' now?", "schedule", { channel_id: it.id, program_start: it.now.start })
    else
        confirm("Record " + txt(it.name) + " for 60 minutes?", "schedule", { channel_id: it.id, minutes: 60 })
    end if
end sub

sub deleteFocused()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    confirm("Delete recording '" + txt(it.title) + "'?", "delete", { id: it.id })
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press then return false
    if m.video.visible
        if key = "back"
            stopVideo()
            return true
        end if
        return false
    end if
    if key = "back"
        if m.mode = "programs"
            showTab(1)
            m.content.setFocus(true)
            return true
        else if m.content.hasFocus()
            m.menu.setFocus(true)
            return true
        end if
        return false
    end if
    if key = "left" and m.content.hasFocus()
        m.menu.setFocus(true)
        return true
    end if
    if key = "right" and m.menu.hasFocus() and m.items.count() > 0
        m.content.setFocus(true)
        return true
    end if
    if key = "options" and m.content.hasFocus()
        if m.mode = "live"
            recordFocused()
            return true
        else if m.mode = "recordings"
            deleteFocused()
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

sub play(url as String, title as String, isLive as Boolean)
    c = CreateObject("roSGNode", "ContentNode")
    c.url = url
    c.title = title
    c.streamFormat = "hls"
    c.live = isLive
    m.video.content = c
    m.video.visible = true
    m.video.setFocus(true)
    m.video.control = "play"
end sub

sub stopVideo()
    m.video.control = "stop"
    m.video.visible = false
    m.content.setFocus(true)
end sub

sub onVideoState()
    st = m.video.state
    if st = "error"
        stopVideo()
        toast("Playback error: " + txt(m.video.errorMsg) + " (code " + txt(m.video.errorCode) + ")")
    else if st = "finished"
        stopVideo()
    end if
end sub

' ------------------------------------------------------------------ API responses

sub loadStatus()
    if m.server <> "" then api("/status", "status")
end sub

sub onApiError(ev as Object)
    t = ev.getRoSGNode()
    if t.tag = "status"
        m.status.text = "Cannot reach " + m.server
    else
        toast("Error: " + t.error)
        if t.tag = "channels" or t.tag = "guidechannels" or t.tag = "recordings" or t.tag = "schedules"
            setRows([], [], "Cannot reach the Pi at " + m.server + ". Check Settings.")
        end if
    end if
end sub

sub onApiResponse(ev as Object)
    r = ev.getData()
    tag = r.tag
    if tag = "status"
        m.status.text = txt(r.channels) + " channels  |  " + txt(r.recordings) + " recordings  |  " + m.server
    else if tag = "channels" or tag = "guidechannels"
        if (tag = "channels" and m.mode <> "live") or (tag = "guidechannels" and m.mode <> "guide") then return
        labels = []
        for each ch in r.items
            line = txt(ch.num) + "   " + txt(ch.name)
            if ch.now <> invalid then line = line + "   -   " + txt(ch.now.title)
            labels.push(line)
        end for
        setRows(labels, r.items, "No channels enabled. On the Pi web page go to Settings > Channel groups and tick the groups you watch.")
    else if tag = "programs"
        if m.mode <> "programs" then return
        labels = []
        for each p in r.programs
            mark = ""
            if p.scheduled = true then mark = "  [REC]"
            labels.push(fmtDay(p.start) + "  " + fmtTime(p.start) + " - " + fmtTime(p["stop"]) + "   " + txt(p.title) + mark)
        end for
        setRows(labels, r.programs, "No guide data for this channel.")
    else if tag = "recordings"
        if m.mode <> "recordings" then return
        labels = []
        for each rec in r.items
            tagTxt = ""
            if rec.status = "recording" then tagTxt = "  [RECORDING]"
            if rec.status = "failed" then tagTxt = "  [FAILED]"
            labels.push(fmtDay(rec.start) + " " + fmtTime(rec.start) + "   " + txt(rec.title) + "   (" + txt(rec.channel_name) + ", " + fmtSize(rec.size_bytes) + ")" + tagTxt)
        end for
        setRows(labels, r.items, "No recordings yet. Press * on a live channel or pick a show in the Guide.")
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
        if m.mode = "programs" then api("/epg/" + txt(m.guideChannel.id) + "?hours=48", "programs")
    else if tag = "cancel_ok"
        toast("Cancelled")
        api("/schedules", "schedules")
    else if tag = "delete_ok"
        toast("Deleted")
        api("/recordings", "recordings")
    else if tag = "refresh"
        if r.started = true then toast("Import started on server; it may take a few minutes") else toast("Import already running")
        loadStatus()
    end if
end sub
