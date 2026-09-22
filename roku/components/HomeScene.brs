' ------------------------------------------------------------------ init

sub init()
    m.menu = m.top.findNode("menu")
    m.content = m.top.findNode("content")
    m.grid = m.top.findNode("grid")
    m.guideCover = m.top.findNode("guideCover")
    m.guideInfo = m.top.findNode("guideInfo")
    m.guideInfoBg = m.top.findNode("guideInfoBg")
    m.detailBg = m.top.findNode("detailBg")
    m.menuBar = m.top.findNode("menuBar")
    m.heading = m.top.findNode("heading")
    m.detail = m.top.findNode("detail")
    m.empty = m.top.findNode("empty")
    m.hint = m.top.findNode("hint")
    m.status = m.top.findNode("status")
    m.video = m.top.findNode("video")
    m.playFocus = m.top.findNode("playFocus")
    m.video.enableUI = true
    m.readyTimer = m.top.findNode("readyTimer")
    m.readyTimer.observeField("fire", "onReadyCheck")
    m.retryTimer = m.top.findNode("retryTimer")
    m.retryTimer.observeField("fire", "onRetry")
    m.playTimer = m.top.findNode("playTimer")
    m.playTimer.observeField("fire", "onPlayTimeout")
    m.stallTimer = m.top.findNode("stallTimer")
    m.stallTimer.observeField("fire", "onStallCheck")
    m.vodInfoTimer = m.top.findNode("vodInfoTimer")
    m.vodInfoTimer.observeField("fire", "onVodInfoTimer")
    m.statusTimer = m.top.findNode("statusTimer")

    m.tabs = ["Favorites", "Guide", "Recent", "Search", "Categories", "Movies", "Sports Teams", "Recordings", "Scheduled", "Settings"]
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
    m.vodId = -1
    m.vodResume = 0
    m.vodOffset = 0
    m.vodGroup = ""
    m.resumeIsVod = false
    m.playTitle = ""
    m.streamUrl = ""
    m.isLive = false
    m.startPos = 0
    m.guideOverlay = false
    m.overlayFocus = "pane"   ' pane | menubar (when the guide overlay is up)
    m.overlayTab = 2          ' index into m.tabs; starts on Recent, then remembers the last one used
    m.barFocus = 2
    m.barLabels = []
    m.focusResults = false
    m.teams = []
    m.sportItems = []
    m.pendingTeam = ""
    m.renameIdx = -1
    m.retryCount = 0
    m.retrying = false
    m.seenPlaying = false
    m.stallPos = -1
    m.stallTicks = 0
    m.rejoinPos = -1
    m.autoRetunes = 0
    m.playChannel = invalid
    m.finishPos = 0
    m.lastErr = ""
    m.lastSegs = 0
    m.readyAttempts = 0
    m.followChannelId = -1
    m.pendingVodInfo = -1
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

' Movie resume positions live under vpos_ so they can't collide with recording ids.
function readVodPos(vodId as Dynamic) as Float
    if vodId = invalid then return 0
    v = m.reg.read("vpos_" + vodId.toStr())
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
        openGrid("favorites=1", "Favorites", "Only channels you starred. Press * to arrange or remove.")
    else if tabName = "Guide"
        openGrid("", "Guide", "All enabled channels")
    else if tabName = "Search"
        m.mode = "list"
        m.hint.text = "OK: search channels and shows"
        if m.lastQuery <> ""
            m.heading.text = "Search: " + m.lastQuery
            api("/search?q=" + urlEnc(m.lastQuery), "search")
        else
            setRows([], [], "Press OK to search channels and shows (e.g. ABC, ESPN, Astros).")
        end if
    else if tabName = "Recent"
        m.mode = "recent"
        m.hint.text = "OK: watch   *: remove"
        renderRecents()
    else if tabName = "Categories"
        m.mode = "categories"
        m.hint.text = "OK: open category guide"
        api("/groups", "groups")
    else if tabName = "Movies"
        m.mode = "vodcats"
        m.hint.text = "OK: open category"
        api("/vod/groups", "vodgroups")
    else if tabName = "Sports Teams"
        m.mode = "teams"
        m.hint.text = "OK: team options (find / rename / delete)   Back: menu"
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
    setRows(["Server address: " + m.server, "Refresh playlist and guide on server", "Clear all movie resume marks", "Restart the Pi server", "Version 1.1 build 32"], ["server", "refresh", "vodclear", "restart", "version"])
end sub

sub onContentFocused()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    if m.mode = "list"
        if it.kind = "program"
            p = it.p
            m.detail.text = txt(p.channel_name) + "   " + fmtDay(p.start) + " " + fmtTime(p.start) + " - " + fmtTime(p["stop"])
        else
            ch = it.ch
            info = txt(ch.grp)
            if ch.now <> invalid
                info = info + "   |   Now: " + txt(ch.now.title) + " (" + fmtTime(ch.now.start) + " - " + fmtTime(ch.now["stop"]) + ")"
            end if
            if ch["next"] <> invalid then info = info + "   |   Next: " + txt(ch["next"].title)
            m.detail.text = info
        end if
    else if m.mode = "categories"
        m.detail.text = txt(it.count) + " channels"
    else if m.mode = "vodcats"
        if it.search = true
            m.detail.text = "Search all movies by name"
        else
            m.detail.text = txt(it.count) + " movies"
        end if
    else if m.mode = "vodlist"
        m.detail.text = txt(it.grp)
        ' Debounced: fetch plot/rating only after the highlight rests ~0.6 s,
        ' so fast scrolling doesn't fire a provider lookup per row.
        if it.id <> invalid
            m.pendingVodInfo = it.id
            m.vodInfoTimer.control = "stop"
            m.vodInfoTimer.control = "start"
        end if
    else if m.mode = "recent"
        if it.kind = "movie"
            m.detail.text = "Movie"
        else
            m.detail.text = txt(it.title)
        end if
    else if m.mode = "recordings"
        m.detail.text = txt(it.description)
    else if m.mode = "teams"
        m.detail.text = txt(it.name)
    else if m.mode = "scheduled"
        m.detail.text = fmtDay(it.start) + " " + fmtTime(it.start) + " - " + fmtTime(it["stop"])
    else
        m.detail.text = ""
    end if
    ' Overlay: list rows show their details in the bottom panel instead of the hidden detail bar.
    if m.guideOverlay then m.guideInfo.text = m.detail.text
end sub

sub onVodInfoTimer()
    if m.mode <> "vodlist" or m.pendingVodInfo < 0 then return
    api("/vod/" + m.pendingVodInfo.toStr() + "/info", "vodinfo")
end sub

sub onContentSelected()
    if m.guideOverlay then hideGuideOverlay()
    i = m.content.itemSelected
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    if m.mode = "list"
        if it.kind = "program"
            programMenu(it.p)
        else
            channelMenu(it.ch, it.ch.now)
        end if
    else if m.mode = "categories"
        openGrid("group=" + urlEnc(it.name), txt(it.name), txt(it.count) + " channels in this category")
        m.grid.setFocus(true)
    else if m.mode = "vodcats"
        if it.search = true
            promptVodSearch()
        else
            m.mode = "vodlist"
            m.vodGroup = txt(it.name)
            m.heading.text = "Movies: " + txt(it.name)
            m.hint.text = "OK: play movie   *: clear resume mark   Back: categories"
            api("/vod?group=" + urlEnc(txt(it.name)), "vodlist")
        end if
    else if m.mode = "vodlist"
        selectVodItem(it)
    else if m.mode = "recent"
        if it.kind = "movie"
            selectVodItem(it)
        else
            playChannel({ id: it.id, name: it.name })
        end if
    else if m.mode = "teams"
        if it.action = "add" then
            promptTeamAdd()
        else
            teamMenu(it.name, i - 1)
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
        else if it = "restart"
            confirm("Restart the Pi server? Streams stop and it takes ~10 seconds to come back.", "restart", {})
        else if it = "vodclear"
            n = 0
            for each k in m.reg.getKeyList()
                if Left(k, 5) = "vpos_"
                    m.reg.delete(k)
                    n = n + 1
                end if
            end for
            m.reg.flush()
            toast("Cleared " + n.toStr() + " saved movie position(s)")
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
    it = m.items[i]
    if it.kind = "channel" then toggleFavorite(it.ch)
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
    if ch = invalid then return
    if m.gridFilter = "favorites=1"
        ' In the Favorites grid * opens the arrange menu instead of unstarring.
        favMoveMenu(ch)
    else
        toggleFavorite(ch)
    end if
end sub

' Arrange options for a favorite channel (* in the Favorites grid).
sub favMoveMenu(ch as Object)
    d = CreateObject("roSGNode", "Dialog")
    d.title = txt(ch.name)
    d.message = "Arrange this channel in your Favorites list"
    d.buttons = ["Move up", "Move down", "Move to top", "Move to bottom", "Remove from Favorites", "Close"]
    d.addField("actions", "array", false)
    d.addField("channelId", "integer", false)
    d.actions = ["up", "down", "top", "bottom", "unfav", "close"]
    d.channelId = ch.id
    d.observeField("buttonSelected", "onFavMove")
    m.top.dialog = d
    d.setFocus(true)
end sub

sub onFavMove(ev as Object)
    d = ev.getRoSGNode()
    d.close = true
    m.top.dialog = invalid
    idx = ev.getData()
    if idx < 0 or idx >= d.actions.count() then return
    action = d.actions[idx]
    if action = "close" then return
    if action = "unfav"
        api("/channels/" + d.channelId.toStr() + "/favorite", "favmove", "POST", "{""favorite"": false}")
    else
        ' Follow the moved channel to its new row after the grid reloads.
        m.followChannelId = d.channelId
        api("/favorites/move", "favmove", "POST", FormatJson({ channel_id: d.channelId, dir: action }))
    end if
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
    if m.guideOverlay
        m.guideInfo.text = m.grid.detail
    else if m.mode = "grid"
        m.detail.text = m.grid.detail
    end if
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
        if key = "back" and m.guideOverlay
            hideGuideOverlay()
            return true
        end if
        if m.guideOverlay and m.overlayFocus = "menubar"
            if key = "left"
                if m.barFocus > 0 then m.barFocus = m.barFocus - 1
                updateMenuBar()
                return true
            else if key = "right"
                if m.barFocus < m.tabs.count() - 1 then m.barFocus = m.barFocus + 1
                updateMenuBar()
                return true
            else if key = "down"
                m.overlayFocus = "pane"
                tn = m.tabs[m.overlayTab]
                if tn = "Guide" or tn = "Favorites" then m.grid.setFocus(true) else m.content.setFocus(true)
                return true
            else if key = "OK"
                applyOverlayTab(m.barFocus)
                return true
            end if
            return true
        end if
        if key = "left" and m.guideOverlay and m.content.hasFocus()
            m.overlayFocus = "menubar"
            m.menuBar.setFocus(true)
            return true
        end if
        if key = "back"
            if m.top.dialog <> invalid
                if m.top.dialog.loading = true
                    stopVideo()
                else
                    m.top.dialog.close = true
                    m.top.dialog = invalid
                    focusVideo()
                end if
                return true
            end if
            stopVideo()
            return true
        else if key = "down" or key = "OK"
            if m.top.dialog = invalid and not m.guideOverlay then trickMenu()
            return true
        else if key = "play" or key = "pause" or key = "playpause"
            if m.video.state = "paused" then m.video.control = "resume" else m.video.control = "pause"
            return true
        else if key = "fastforward"
            p = m.video.position + 30
            dur = m.video.duration
            if dur <> invalid and dur > 0 and p > dur - 10 then p = dur - 10
            if p > 0 then m.video.seek = p
            return true
        else if key = "rewind"
            p = m.video.position - 30
            if p < 0 then p = 0
            m.video.seek = p
            return true
        else if key = "up"
            if m.top.dialog = invalid and not m.guideOverlay
                showGuideOverlay()
                return true
            else if m.guideOverlay and m.overlayFocus = "pane"
                m.overlayFocus = "menubar"
                m.menuBar.setFocus(true)
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
        if m.content.hasFocus() and m.mode = "vodlist"
            ' Back out of a movie list (category or search results) to categories.
            m.mode = "vodcats"
            m.heading.text = "Movies"
            m.hint.text = "OK: open category"
            api("/vod/groups", "vodgroups")
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
            else if m.mode = "vodlist"
                clearVodResume()
                return true
            else if m.mode = "recent"
                removeRecentFocused()
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
    else if d.action = "restart"
        api("/restart", "restart_ok", "POST", "{}")
    end if
end sub

sub promptVodSearch()
    k = CreateObject("roSGNode", "KeyboardDialog")
    k.title = "Search movies"
    k.message = "Type part of a movie name"
    k.text = ""
    k.buttons = ["Search", "Cancel"]
    k.observeField("buttonSelected", "onVodSearchEntered")
    m.top.dialog = k
end sub

sub onVodSearchEntered(ev as Object)
    k = ev.getRoSGNode()
    if ev.getData() = 0
        q = k.text.trim()
        if q <> ""
            m.mode = "vodlist"
            m.vodGroup = ""
            m.heading.text = "Movies: '" + q + "'"
            m.hint.text = "OK: play movie   *: clear resume mark   Back: categories"
            api("/vod?q=" + urlEnc(q), "vodlist")
        end if
    end if
    k.close = true
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
            api("/search?q=" + urlEnc(q), "search")
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

' OK on a team: choose what to do with it instead of always searching.
sub teamMenu(name as String, idx as Integer)
    d = CreateObject("roSGNode", "Dialog")
    d.title = name
    d.message = "Find a live game, or edit this team."
    d.buttons = ["Find game", "Rename", "Delete", "Cancel"]
    d.addField("teamIdx", "integer", false)
    d.teamIdx = idx
    d.observeField("buttonSelected", "onTeamMenu")
    m.top.dialog = d
end sub

sub onTeamMenu(ev as Object)
    d = ev.getRoSGNode()
    btn = ev.getData()
    idx = d.teamIdx
    d.close = true
    m.top.dialog = invalid
    if idx < 0 or idx >= m.teams.count() then return
    if btn = 0
        findGame(m.teams[idx])
    else if btn = 1
        promptTeamRename(idx)
    else if btn = 2
        m.teams.delete(idx)
        m.reg.write("teams", FormatJson(m.teams))
        m.reg.flush()
        loadTeams()
    end if
end sub

sub promptTeamRename(idx as Integer)
    m.renameIdx = idx
    k = CreateObject("roSGNode", "KeyboardDialog")
    k.title = "Rename sports team"
    k.message = "Edit the team name"
    k.text = m.teams[idx]
    k.buttons = ["Save", "Cancel"]
    k.observeField("buttonSelected", "onTeamEntered")
    m.top.dialog = k
end sub

sub onTeamEntered(ev as Object)
    k = ev.getRoSGNode()
    if ev.getData() = 0
        name = k.text.trim()
        if name <> ""
            if m.renameIdx >= 0 and m.renameIdx < m.teams.count()
                m.teams[m.renameIdx] = name
                m.reg.write("teams", FormatJson(m.teams))
                m.reg.flush()
                loadTeams()
            else
                addTeam(name)
            end if
        end if
    end if
    m.renameIdx = -1
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
    m.seenPlaying = false
    m.stallPos = -1
    m.stallTicks = 0
    m.rejoinPos = -1
    m.finishPos = 0
    m.readyTimer.control = "stop"
    m.retryTimer.control = "stop"
    m.stallTimer.control = "start"
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
    ' Play the recording buffer as VOD even for live: joining at the live edge of a
    ' still-growing playlist left ~one segment of runway and the player reported
    ' "finished" the instant it stalled. VOD mode plays from position 0, gives the
    ' seek slider the whole buffer, and keeps re-fetching until ENDLIST.
    c.live = false
    m.video.content = c
    m.video.loop = false
    m.video.visible = true
    focusVideo()
    m.video.control = "play"
    m.playTimer.control = "start"
end sub

' Reload the live playlist and rejoin. A fresh ContentNode is required:
' "play" on a Video that has finished replays the manifest it already has instead of
' fetching the (now longer) one from the Pi.
' m.rejoinPos >= 0: continue from that position (stall recovery - don't lose what
' aired during the gap). < 0: jump to the live edge (manual "Jump to live").
sub rejoinLive()
    resumePos = m.rejoinPos
    m.rejoinPos = -1
    ' Grab duration before stopping - "stop" can reset it.
    dur = m.video.duration
    if m.video.state = "playing" or m.video.state = "paused" or m.video.state = "buffering"
        m.video.control = "stop"
    end if
    c = CreateObject("roSGNode", "ContentNode")
    c.url = m.streamUrl
    c.title = m.playTitle
    c.streamFormat = "hls"
    c.live = false
    if resumePos >= 0
        c.playStart = resumePos
    else if dur <> invalid and dur > 15
        c.playStart = dur - 10
    else if m.finishPos > 4
        c.playStart = m.finishPos - 3
    end if
    m.video.content = c
    m.video.visible = true
    m.seenPlaying = false
    m.stallPos = -1
    m.stallTicks = 0
    focusVideo()
    m.video.control = "play"
end sub

' Position watchdog: a starving buffer can sit in "buffering" (or even "playing")
' forever without ever firing "error"/"finished", so no recovery path triggers.
' If the position hasn't advanced ~15s after playback had started, treat it like a
' "finished" that never fired and go through the normal rejoin flow.
sub onStallCheck()
    if not m.video.visible or not m.isLive or not m.seenPlaying then return
    if m.video.state <> "playing" and m.video.state <> "buffering" then return
    curPos = m.video.position
    if curPos = invalid then return
    if m.stallPos >= 0 and curPos <= m.stallPos + 0.5
        m.stallTicks = m.stallTicks + 1
    else
        m.stallTicks = 0
        m.stallPos = curPos
    end if
    if m.stallTicks >= 3 and not m.retrying
        m.stallTicks = 0
        m.stallPos = -1
        m.finishPos = curPos
        m.retryCount = m.retryCount + 1
        if m.retryCount < 30
            m.retrying = true
            m.retryTimer.control = "start"
        else
            playError("The live stream kept freezing (" + txt(m.lastSegs) + " segments buffered). " + m.lastErr)
        end if
    end if
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

' Horizontal tab bar across the top of the guide overlay (YouTube-TV-style top nav).
sub buildMenuBar()
    if m.menuBar.getChildCount() > 0 then return
    m.barSel = CreateObject("roSGNode", "Rectangle")
    m.barSel.height = 3
    m.barSel.color = "#3D6BFF"
    m.menuBar.appendChild(m.barSel)
    widths = [130, 85, 100, 95, 140, 110, 150, 140, 125, 110]
    x = 0
    for i = 0 to m.tabs.count() - 1
        w = 100
        if i < widths.count() then w = widths[i]
        l = CreateObject("roSGNode", "Label")
        l.translation = [x, 0]
        l.width = w
        l.height = 46
        l.text = m.tabs[i]
        l.font = "font:SmallestSystemFont"
        l.color = "#8894A0"
        l.vertAlign = "center"
        m.menuBar.appendChild(l)
        m.barLabels.push(l)
        x += w
    end for
end sub

sub updateMenuBar()
    scroll = 0
    ' scroll the bar so the focused label stays inside the ~900px window
    for i = 0 to m.barLabels.count() - 1
        l = m.barLabels[i]
        if i = m.barFocus
            l.color = "#FFFFFF"
            lx = l.translation[0]
            if lx + l.width - scroll > 900 then scroll = lx + l.width - 900
            if lx - scroll < 0 then scroll = lx
        else
            l.color = "#8894A0"
        end if
        if i = m.overlayTab
            m.barSel.translation = [l.translation[0], 44]
            m.barSel.width = l.width
        end if
    end for
    m.menuBar.translation = [40 - scroll, 56]
end sub

' Show the chosen tab's pane in the left half of the overlay while playback continues.
sub applyOverlayTab(idx as Integer)
    m.overlayTab = idx
    tabName = m.tabs[idx]
    m.overlayFocus = "pane"
    updateMenuBar()
    if tabName = "Guide" or tabName = "Favorites"
        m.mode = "grid"
        m.content.visible = false
        m.grid.visible = true
        m.gridFilter = ""
        if tabName = "Favorites" then m.gridFilter = "favorites=1"
        m.gridFrom = 0
        loadGrid()
        m.grid.setFocus(true)
    else
        m.grid.visible = false
        m.content.visible = true
        m.content.translation = [40, 150]
        m.content.itemSize = [890, 56]
        m.content.numRows = 8
        if tabName = "Search"
            m.mode = "list"
            promptSearch()
        else if tabName = "Recent"
            m.mode = "recent"
            renderRecents()
            m.content.setFocus(true)
        else if tabName = "Categories"
            m.mode = "categories"
            api("/groups", "groups")
            m.content.setFocus(true)
        else if tabName = "Movies"
            m.mode = "vodcats"
            api("/vod/groups", "vodgroups")
            m.content.setFocus(true)
        else if tabName = "Sports Teams"
            m.mode = "teams"
            loadTeams()
            m.content.setFocus(true)
        else if tabName = "Recordings"
            m.mode = "recordings"
            api("/recordings", "recordings")
            m.content.setFocus(true)
        else if tabName = "Scheduled"
            m.mode = "scheduled"
            api("/schedules", "schedules")
            m.content.setFocus(true)
        else if tabName = "Settings"
            m.mode = "settings"
            showSettings()
            m.content.setFocus(true)
        end if
    end if
end sub

sub showGuideOverlay()
    m.guideOverlay = true
    m.overlayFocus = "pane"
    ' Reopen on whichever tab was used last (Recent the first time).
    m.barFocus = m.overlayTab
    buildMenuBar()
    updateMenuBar()
    if m.grid.data = invalid then loadGrid()
    ' Solid cover so the list/detail rows underneath don't bleed through the grid gaps.
    m.guideCover.visible = true
    m.empty.visible = false
    m.detail.visible = false
    m.detailBg.visible = false
    m.guideInfoBg.visible = true
    m.guideInfo.visible = true
    m.guideInfo.text = txt(m.grid.detail)
    m.menuBar.visible = true
    m.video.translation = [960, 0]
    m.video.width = 960
    m.video.height = 1080
    m.grid.translation = [40, 110]
    m.grid.scale = [0.66, 0.66]
    applyOverlayTab(m.overlayTab)
    m.hint.text = "OK: menu   Back: close guide"
end sub

sub hideGuideOverlay()
    m.guideOverlay = false
    m.overlayFocus = "pane"
    m.guideCover.visible = false
    m.guideInfo.visible = false
    m.guideInfoBg.visible = false
    m.menuBar.visible = false
    m.content.translation = [470, 180]
    m.content.itemSize = [1390, 58]
    m.content.numRows = 11
    m.content.visible = (m.mode <> "grid")
    m.detail.visible = true
    m.detailBg.visible = true
    m.video.translation = [0, 0]
    m.video.width = 1920
    m.video.height = 1080
    m.grid.visible = false
    m.grid.translation = [470, 170]
    m.grid.scale = [1, 1]
    focusVideo()
    m.hint.text = "OK/Down: playback controls   Up: guide   Back: stop"
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
    if idx < 0
        m.resumeIsVod = false
        return
    end if
    startPos = 0
    if idx = 0 then startPos = m.resumePos
    if m.resumeIsVod
        m.resumeIsVod = false
        vodPlayStart(startPos)
        return
    end if
    m.recordingId = m.resumeRecordingId
    play(m.resumeUrl, m.resumeTitle, m.resumeLive, startPos)
end sub

' Shared by the Movies list and the Recent tab.
sub selectVodItem(it as Object)
    m.vodId = it.id
    m.playTitle = txt(it.name)
    saved = readVodPos(it.id)
    if saved > 5
        ' Ask first: the Pi starts muxing at the chosen position, so the pick
        ' has to happen before the /play call.
        m.resumeIsVod = true
        showResumeDialog("", txt(it.name), false, saved, -1)
    else
        vodPlayStart(0)
    end if
end sub

' ------------------------------------------------------------------ recently watched

function loadRecents() as Object
    s = m.reg.read("recent")
    if s = invalid or s = "" then return []
    arr = ParseJson(s)
    if type(arr) <> "roArray" then return []
    return arr
end function

sub addRecent(item as Object)
    ' Dedupe by kind+id, put the newest at the top, cap the list.
    recents = []
    for each r in loadRecents()
        if not (r.id = item.id and r.kind = item.kind) then recents.push(r)
    end for
    recents.unshift(item)
    while recents.count() > 15
        recents.pop()
    end while
    m.reg.write("recent", FormatJson(recents))
    m.reg.flush()
end sub

function recentLabel(r as Object) as String
    if r.kind = "movie" then return "MOVIE   " + txt(r.name)
    line = txt(r.name)
    if txt(r.title) <> "" then line = line + "   -   " + txt(r.title)
    return line
end function

sub renderRecents()
    m.items = loadRecents()
    labels = []
    for each r in m.items
        labels.push(recentLabel(r))
    end for
    setRows(labels, m.items, "Nothing watched yet. Tune a channel or play a movie and it shows up here.")
end sub

sub removeRecentFocused()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    m.items.delete(i)
    m.reg.write("recent", FormatJson(m.items))
    m.reg.flush()
    renderRecents()
    toast("Removed")
end sub

' * key on a movie: forget its saved position so it starts from the beginning.
sub clearVodResume()
    i = m.content.itemFocused
    if i < 0 or i >= m.items.count() then return
    it = m.items[i]
    m.reg.delete("vpos_" + it.id.toStr())
    m.reg.flush()
    api("/vod?group=" + urlEnc(m.vodGroup), "vodlist")
    toast("Resume point cleared")
end sub

' Start the movie mux on the Pi at the chosen position (resume point or 0).
sub vodPlayStart(startPos as Float)
    m.vodResume = startPos
    showLoading("Loading movie...")
    api("/vod/" + m.vodId.toStr() + "/play?pos=" + Int(startPos).toStr(), "vodplay", "POST", "{}")
end sub

' Search hit a program (not a channel): offer watch/record for that airing.
sub programMenu(p as Object)
    d = CreateObject("roSGNode", "Dialog")
    d.title = txt(p.title)
    d.message = txt(p.channel_name) + "   " + fmtDay(p.start) + " " + fmtTime(p.start) + " - " + fmtTime(p["stop"])
    buttons = []
    actions = []
    if p.now_playing = true
        buttons.push("Watch now")
        actions.push("watch")
    else
        buttons.push("Watch channel now")
        actions.push("watch")
    end if
    if p.scheduled = true
        buttons.push("Cancel recording")
        actions.push("unschedule")
    else
        buttons.push("Record")
        actions.push("record")
    end if
    buttons.push("Close")
    actions.push("close")
    d.buttons = buttons
    d.addField("actions", "array", false)
    d.addField("program", "assocarray", false)
    d.actions = actions
    d.program = p
    d.observeField("buttonSelected", "onProgramMenu")
    m.top.dialog = d
end sub

sub onProgramMenu(ev as Object)
    d = ev.getRoSGNode()
    d.close = true
    idx = ev.getData()
    if idx < 0 or idx >= d.actions.count() then return
    action = d.actions[idx]
    p = d.program
    if action = "watch"
        playChannel({ id: p.channel_id, name: p.channel_name })
    else if action = "record"
        api("/schedules", "scheduled_ok", "POST", FormatJson({ channel_id: p.channel_id, program_start: p.start }))
    else if action = "unschedule"
        api("/schedules/by-program?channel_id=" + txt(p.channel_id) + "&start=" + txt(p.start), "cancel_ok", "DELETE", "")
    end if
end sub

sub onSportsChannel(ev as Object)
    idx = ev.getData()
    if idx < 0 or m.sportItems = invalid then return
    if idx < m.sportItems.count()
        m.pendingChannel = m.sportItems[idx]
        m.top.dialog = invalid
        playChannel(m.sportItems[idx])
    end if
end sub

sub playChannel(ch as Object)
    m.pendingChannel = ch
    m.recordingId = -1
    m.vodId = -1
    m.playTitle = ch.name
    showLoading("Tuning...")
    api("/timeshift", "timeshift", "POST", FormatJson({ channel_id: ch.id }))
end sub

sub trickMenu()
    if m.recordingId < 0 and m.vodId < 0 then return
    d = CreateObject("roSGNode", "Dialog")
    d.title = m.playTitle
    posn = m.video.position
    dur = m.video.duration
    info = "OK: select   Back: close"
    if posn <> invalid
        info = "Position " + fmtDuration(posn)
        if dur <> invalid and dur > 0 then info = info + " / " + fmtDuration(dur)
        if m.isLive then info = info + "   (live buffer)"
        info = info + Chr(10) + "OK: select   Back: close"
    end if
    d.message = info
    if m.vodId >= 0
        ' Movies have no live edge to jump to and nothing to keep.
        if m.video.state = "paused"
            buttons = ["Play", "Back 30s", "Forward 30s"]
            actions = ["play", "back30", "fwd30"]
        else
            buttons = ["Pause", "Back 30s", "Forward 30s"]
            actions = ["pause", "back30", "fwd30"]
        end if
    else if m.video.state = "paused"
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
        rejoinLive()
    else if action = "keep"
        if d.recordingId > 0
            api("/timeshift/" + d.recordingId.toStr() + "/keep", "keep", "POST", "")
        end if
    end if
    focusVideo()
end sub

' During playback the Video node keeps focus: its built-in UI handles OK/FF/RW/play and
' shows the seek slider. Up/Down/Back aren't transport keys so they still bubble to
' onKeyEvent (Up = guide overlay, Down/OK fallback = controls menu, Back = stop).
sub focusVideo()
    m.video.setFocus(true)
end sub

sub stopVideo(clearResume = false)
    key = ""
    if m.vodId >= 0
        key = "vpos_" + m.vodId.toStr()
    else if m.recordingId >= 0
        key = "pos_" + m.recordingId.toStr()
    end if
    if key <> ""
        if clearResume
            m.reg.delete(key)
            m.reg.flush()
        else
            lastPos = m.video.position
            totalDur = m.video.duration
            if lastPos > 5 and (totalDur <= 0 or lastPos < totalDur - 15)
                m.reg.write(key, lastPos.toStr())
                m.reg.flush()
            end if
        end if
    end if
    hideLoading()
    m.readyTimer.control = "stop"
    m.playTimer.control = "stop"
    m.stallTimer.control = "stop"
    m.seenPlaying = false
    m.stallPos = -1
    m.stallTicks = 0
    m.video.control = "stop"
    m.video.visible = false
    if m.guideOverlay
        m.guideOverlay = false
        m.overlayFocus = "pane"
        m.guideCover.visible = false
        m.guideInfo.visible = false
        m.guideInfoBg.visible = false
        m.menuBar.visible = false
        m.content.translation = [470, 180]
        m.content.itemSize = [1390, 58]
        m.content.numRows = 11
        m.content.visible = (m.mode <> "grid")
        m.detail.visible = true
        m.detailBg.visible = true
        m.video.translation = [0, 0]
        m.video.width = 1920
        m.video.height = 1080
        m.grid.visible = false
        m.grid.translation = [470, 170]
        m.grid.scale = [1, 1]
    end if
    if m.vodId >= 0
        ' Release the provider connection and the muxed files on the Pi right away
        ' instead of letting them sit until the idle reaper runs.
        api("/vod/" + m.vodId.toStr() + "/stop", "vodstop", "POST", "")
    end if
    m.recordingId = -1
    m.vodId = -1
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
        m.seenPlaying = true
        m.stallPos = -1
        m.stallTicks = 0
        focusVideo()
        if m.startPos > 0
            m.video.seek = m.startPos
            m.startPos = 0
        end if
        m.retryCount = 0
        m.retrying = false
        m.autoRetunes = 0
    else if st = "error"
        if m.video.position <> invalid and m.video.position > 4 then m.finishPos = m.video.position
        if m.recordingId >= 0 and m.isLive and m.retryCount < 5 and not m.retrying
            m.retrying = true
            m.retryCount = m.retryCount + 1
            m.retryTimer.control = "start"
        else
            playError("Playback error: " + txt(m.video.errorMsg) + " (code " + txt(m.video.errorCode) + ")" + Chr(10) + m.streamUrl)
        end if
    else if st = "finished"
        ' A live buffer only really ends when the Pi writes ENDLIST; if the player ran off the
        ' end of the growing playlist, rejoin once the buffer has grown instead of stopping.
        if m.recordingId >= 0 and m.isLive and m.retryCount < 30 and not m.retrying
            m.retrying = true
            m.retryCount = m.retryCount + 1
            if m.video.position <> invalid then m.finishPos = m.video.position
            m.retryTimer.control = "start"
        else
            if m.isLive
                playError("The live buffer stopped producing video (" + txt(m.lastSegs) + " segments). " + m.lastErr)
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
    if m.vodId >= 0
        api("/vod/" + m.vodId.toStr() + "/ready", "readycheck")
    else if m.recordingId >= 0
        api("/timeshift/" + m.recordingId.toStr() + "/ready", "readycheck")
    end if
end sub

' ------------------------------------------------------------------ API responses

sub loadStatus()
    if m.server = "" then return
    api("/status", "status")
    ' Heartbeat so the Pi keeps the live buffer (or movie muxer) running while we watch.
    if m.video.visible and m.isLive and m.recordingId >= 0
        api("/timeshift/" + m.recordingId.toStr() + "/touch", "touch", "POST", "")
    else if m.video.visible and m.vodId >= 0
        api("/vod/" + m.vodId.toStr() + "/touch", "touch", "POST", "")
    end if
end sub

sub onApiError(ev as Object)
    t = ev.getRoSGNode()
    if t.tag = "touch" or t.tag = "vodinfo" then return
    if t.tag = "timeshift" or t.tag = "readycheck" or t.tag = "rejoin" or t.tag = "vodplay"
        m.readyTimer.control = "stop"
        playError("The Pi did not answer " + t.url + Chr(10) + t.error + Chr(10) + "If this says HTTP 404, the Pi is running old server code: cd pi-iptv-dvr && git pull && sudo systemctl restart pi-iptv-dvr")
        return
    end if
    if t.tag = "status"
        m.status.text = "Cannot reach " + m.server
    else
        toast("Error: " + t.error)
        if t.tag = "channels" or t.tag = "groups" or t.tag = "recordings" or t.tag = "schedules" or t.tag = "vodgroups" or t.tag = "vodlist"
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
            m.playChannel = m.pendingChannel
            addRecent({ kind: "channel", id: m.pendingChannel.id, name: txt(m.pendingChannel.name), title: txt(r.title) })
            m.readyAttempts = 0
            m.readyTimer.control = "start"
        else
            playError("Could not start the live buffer: " + txt(r.error))
        end if
    else if tag = "readycheck"
        if m.vodId >= 0
            ' Movie muxer poll: play once a segment exists, fail if ffmpeg died first.
            if r.ready = true or r.ready = 1
                m.readyTimer.control = "stop"
                ' The playlist starts at vodOffset (0 for a full mux, the resume point
                ' for a seek-start), so the player's position is relative to that.
                startPos = m.vodResume - m.vodOffset
                if startPos < 0 then startPos = 0
                play(m.streamUrl, m.playTitle, false, startPos)
            else if r.alive <> true and r.alive <> 1
                m.readyTimer.control = "stop"
                if txt(r.error) <> ""
                    playError("The Pi could not start this movie. ffmpeg said:" + Chr(10) + txt(r.error))
                else
                    playError("The movie stream stopped before it produced any video.")
                end if
            else
                m.readyAttempts = m.readyAttempts + 1
                if m.top.dialog <> invalid and m.top.dialog.loading = true
                    m.top.dialog.title = "Loading movie... " + txt(r.segments) + " segments"
                end if
                if m.readyAttempts > 240
                    m.readyTimer.control = "stop"
                    playError("The movie still is not ready after 4 minutes. The provider may be too slow or the file unavailable.")
                end if
            end if
            return
        end if
        if r.ready = true or r.ready = 1
            m.readyTimer.control = "stop"
            ' Rejoining a buffer that was already recording (e.g. after a playback failure):
            ' start near the live edge instead of replaying from position 0.
            startPos = 0
            if r.duration <> invalid and r.duration > 20 then startPos = r.duration - 10
            play(m.streamUrl, m.playTitle, true, startPos)
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
        m.lastErr = txt(r.error)
        m.lastSegs = r.segments
        if txt(r.status) = "recording"
            ' Only reload once the buffer actually grew past where we stopped (~2+ new
            ' segments); reloading immediately just re-finished and burned the retries.
            if r.duration <> invalid and r.duration > m.finishPos + 6
                ' Resume where playback froze so the viewer doesn't lose what aired
                ' during the gap; near position 0 that's meaningless, so join live.
                if m.finishPos > 4 then m.rejoinPos = m.finishPos + 1
                rejoinLive()
            else
                m.retrying = true
                m.retryTimer.control = "start"
            end if
        else if txt(r.status) = "done"
            ' The buffer finalized while we were watching (show boundary, kept-recording
            ' end, unrecoverable drop). Retune the channel so TV rolls into the next
            ' show instead of just stopping.
            if m.autoRetunes < 3 and m.playChannel <> invalid
                m.autoRetunes = m.autoRetunes + 1
                m.pendingChannel = m.playChannel
                api("/timeshift", "timeshift", "POST", FormatJson({ channel_id: m.playChannel.id }))
            else
                stopVideo(true)
            end if
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
    else if tag = "favmove"
        if m.mode = "grid" or m.guideOverlay then loadGrid()
    else if tag = "guide"
        if m.mode <> "grid" and not m.guideOverlay then return
        ' After a favorites move, keep the highlight on the channel that moved.
        ' focusRow must be set before grid.data: it has no onChange, and onData
        ' clamps it to the new channel list and renders once.
        if m.followChannelId >= 0 and r.channels <> invalid
            for i = 0 to r.channels.count() - 1
                if r.channels[i].id = m.followChannelId
                    m.grid.focusRow = i
                    exit for
                end if
            end for
            m.followChannelId = -1
        end if
        m.grid.data = r
        if m.guideOverlay then m.grid.setFocus(true)
        n = 0
        if r.channels <> invalid then n = r.channels.count()
        if n = 0 and m.gridFilter = "favorites=1"
            m.detail.text = "No favorites yet. Open Guide, Categories or Search, highlight a channel and press * to star it."
        end if
    else if tag = "search" or tag = "channels"
        if m.mode <> "list" then return
        labels = []
        items = []
        chans = r.items
        if chans = invalid then chans = r.channels
        if chans <> invalid
            for each ch in chans
                star = "     "
                if ch.favorite = 1 then star = "*   "
                line = star + txt(ch.num) + "   " + txt(ch.name)
                if ch.now <> invalid then line = line + "   -   " + txt(ch.now.title)
                labels.push(line)
                items.push({ kind: "channel", ch: ch })
            end for
        end if
        if r.programs <> invalid
            for each p in r.programs
                tagTxt = ""
                if p.now_playing = true then tagTxt = "   [ON NOW]"
                if p.scheduled = true then tagTxt = tagTxt + "   [REC]"
                labels.push("TV   " + txt(p.title) + "   " + fmtDay(p.start) + " " + fmtTime(p.start) + "   (" + txt(p.channel_name) + ")" + tagTxt)
                items.push({ kind: "program", p: p })
            end for
        end if
        setRows(labels, items, "Nothing matches '" + m.lastQuery + "'. Channels and show titles/descriptions are searched.")
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
    else if tag = "vodgroups"
        if m.mode <> "vodcats" then return
        labels = ["Search movies"]
        items = [{ name: "", count: 0, search: true }]
        for each g in r.items
            name = txt(g.name)
            if name = "" then name = "(no category)"
            labels.push(name + "   (" + txt(g.count) + ")")
            items.push({ name: g.name, count: g.count })
        end for
        setRows(labels, items, "No movies found. Try Refresh playlist and guide on server from Settings.")
    else if tag = "vodlist"
        if m.mode <> "vodlist" then return
        labels = []
        items = []
        for each mv in r.items
            saved = readVodPos(mv.id)
            tagTxt = ""
            if saved > 5 then tagTxt = "   (resume " + fmtDuration(saved) + ")"
            labels.push(txt(mv.name) + tagTxt)
            items.push(mv)
        end for
        setRows(labels, items, "No movies in this category.")
    else if tag = "vodinfo"
        ' Details for the highlighted movie. Stale responses (user scrolled on)
        ' are dropped by comparing the returned id to the focused row.
        if m.mode <> "vodlist" then return
        i = m.content.itemFocused
        if i < 0 or i >= m.items.count() then return
        it = m.items[i]
        if it.id = invalid or it.id <> r.id then return
        if r.ok = true or r.ok = 1
            parts = []
            mpaa = txt(r.mpaa)
            if mpaa <> "" and mpaa <> "N/A" and mpaa <> "Not Rated" then parts.push("Rated " + mpaa)
            if txt(r.rating) <> "" then parts.push("Score " + txt(r.rating) + " / 5")
            if txt(r.released) <> "" then parts.push(Left(txt(r.released), 4))
            if txt(r.genre) <> "" then parts.push(txt(r.genre))
            if txt(r.duration) <> "" then parts.push(txt(r.duration))
            info = ""
            for each p in parts
                if info <> "" then info = info + "   |   "
                info = info + p
            end for
            if txt(r.plot) <> "" then info = info + Chr(10) + txt(r.plot)
            if txt(r.cast) <> "" then info = info + Chr(10) + "Cast: " + txt(r.cast)
            if info = "" then info = txt(it.grp)
            m.detail.text = info
            if m.guideOverlay then m.guideInfo.text = info
        end if
    else if tag = "vodplay"
        if r.ok = true or r.ok = 1
            ' ffmpeg is muxing on the Pi; poll /ready like the live-buffer flow so a slow
            ' provider (or a big file) can't trip the API task's 15 s timeout.
            m.streamUrl = r.stream_url
            m.playTitle = txt(r.title)
            m.vodOffset = 0
            if r.offset <> invalid then m.vodOffset = r.offset
            addRecent({ kind: "movie", id: m.vodId, name: m.playTitle })
            m.readyAttempts = 0
            m.readyTimer.control = "start"
        else
            playError("Could not start the movie: " + txt(r.error))
        end if
    else if tag = "recordings"
        if m.mode <> "recordings" then return
        labels = []
        for each rec in r.items
            tagTxt = ""
            if rec.status = "recording" then tagTxt = "  [RECORDING]"
            if rec.status = "failed" then tagTxt = "  [FAILED]"
            saved = readResumePos(rec.id)
            if rec.status <> "recording" and saved > 5 and rec.duration <> invalid and rec.duration > 0
                pct = Int(saved / rec.duration * 100)
                if pct > 100 then pct = 100
                tagTxt = tagTxt + "   " + pct.toStr() + "% watched"
            end if
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
        if m.mode = "list" then api("/search?q=" + urlEnc(m.lastQuery), "search")
    else if tag = "delete_ok"
        toast("Deleted")
        api("/recordings", "recordings")
    else if tag = "sports"
        hideLoading()
        if r.items = invalid or r.items.count() = 0
            toast("No live game found for " + txt(m.pendingTeam))
        else if r.items.count() = 1
            playChannel(r.items[0])
        else
            m.sportItems = r.items
            d = CreateObject("roSGNode", "Dialog")
            d.title = "Select channel playing " + txt(m.pendingTeam)
            d.message = "Pick one of the channels below:"
            labels = []
            for each ch in r.items
                line = txt(ch.num) + " " + txt(ch.name)
                if ch.now <> invalid and ch.now.title <> invalid
                    line = line + " - " + txt(ch.now.title)
                end if
                labels.push(line)
            end for
            d.buttons = labels
            d.observeField("buttonSelected", "onSportsChannel")
            m.top.dialog = d
            d.setFocus(true)
        end if
    else if tag = "refresh"
        if r.started = true then toast("Import started on server; it may take a few minutes") else toast("Import already running")
        loadStatus()
    else if tag = "restart_ok"
        toast("Pi server restarting - back in about 10 seconds")
    end if
end sub
