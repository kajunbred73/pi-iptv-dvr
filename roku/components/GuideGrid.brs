' Time-grid guide: channel column on the left, program blocks across a time window.
' Up/Down = channel, Left/Right = program (past the edge pages the window), OK = select, * = favorite.

sub init()
    m.CHAN_W = 300
    m.GRID_W = 1080
    m.ROW_H = 74
    m.GAP = 4
    m.VISIBLE = 8
    m.timebar = m.top.findNode("timebar")
    m.rows = m.top.findNode("rows")
    m.nowline = m.top.findNode("nowline")
    m.focusCol = 0
    m.firstRow = 0
    m.chans = []
    m.top.observeField("focusedChild", "onFocusChange")
end sub

sub onFocusChange()
    render()
end sub

function pxPerMin() as float
    d = m.top.data
    if d = invalid or d["end"] = invalid or d.start = invalid then return 6.0
    mins = (d["end"] - d.start) / 60
    if mins <= 0 then return 6.0
    return m.GRID_W / mins
end function

sub onData()
    d = m.top.data
    if d = invalid or d.channels = invalid then
        m.chans = []
    else
        m.chans = d.channels
    end if
    if m.top.focusRow >= m.chans.count() then m.top.focusRow = 0
    if m.top.focusRow < 0 then m.top.focusRow = 0
    ' keep column pointing at whatever is on now for the focused row
    m.focusCol = colAtNow(m.top.focusRow)
    clampScroll()
    render()
end sub

function colAtNow(row as integer) as integer
    if row >= m.chans.count() then return 0
    progs = m.chans[row].programs
    if progs = invalid then return 0
    d = m.top.data
    t = d.now
    if t = invalid then t = d.start
    for i = 0 to progs.count() - 1
        if progs[i].start <= t and progs[i]["stop"] > t then return i
    end for
    ' otherwise first program in the window
    return 0
end function

sub clampScroll()
    if m.top.focusRow < m.firstRow then m.firstRow = m.top.focusRow
    if m.top.focusRow >= m.firstRow + m.VISIBLE then m.firstRow = m.top.focusRow - m.VISIBLE + 1
    if m.firstRow < 0 then m.firstRow = 0
end sub

function fmtTime(t as integer) as string
    dt = CreateObject("roDateTime")
    dt.FromSeconds(t)
    dt.ToLocalTime()
    h = dt.GetHours()
    mi = dt.GetMinutes()
    ap = "am"
    if h >= 12 then ap = "pm"
    h12 = h mod 12
    if h12 = 0 then h12 = 12
    ms = mi.toStr()
    if mi < 10 then ms = "0" + ms
    return h12.toStr() + ":" + ms + ap
end function

function mkRect(x as integer, y as integer, w as integer, h as integer, color as string) as object
    r = CreateObject("roSGNode", "Rectangle")
    r.translation = [x, y]
    r.width = w
    r.height = h
    r.color = color
    return r
end function

function mkLabel(x as integer, y as integer, w as integer, h as integer, text as string, color as string, fnt as string) as object
    l = CreateObject("roSGNode", "Label")
    l.translation = [x, y]
    l.width = w
    l.height = h
    l.text = text
    l.color = color
    l.font = fnt
    l.vertAlign = "center"
    l.wrap = false
    l.ellipsizeOnBoundary = true
    return l
end function

sub render()
    m.timebar.removeChildrenIndex(m.timebar.getChildCount(), 0)
    m.rows.removeChildrenIndex(m.rows.getChildCount(), 0)
    d = m.top.data
    if d = invalid or d.start = invalid then return
    ppm = pxPerMin()

    ' ---- time bar (every 30 min)
    m.timebar.appendChild(mkLabel(0, 0, m.CHAN_W - 10, 40, fmtDate(d.start), "#8A94A0", "font:SmallestSystemFont"))
    t = d.start
    while t < d["end"]
        x = m.CHAN_W + int((t - d.start) / 60 * ppm)
        m.timebar.appendChild(mkRect(x, 0, 1, 40, "#3A424C"))
        m.timebar.appendChild(mkLabel(x + 8, 0, 160, 40, fmtTime(t), "#C0C8D0", "font:SmallSystemFont"))
        t = t + 1800
    end while
    m.timebar.appendChild(mkRect(0, 41, m.CHAN_W + m.GRID_W, 2, "#3A424C"))

    ' ---- now line
    if d.now <> invalid and d.now >= d.start and d.now < d["end"]
        m.nowline.translation = [m.CHAN_W + int((d.now - d.start) / 60 * ppm), 40]
        m.nowline.height = 4 + m.VISIBLE * (m.ROW_H + m.GAP)
        m.nowline.visible = true
    else
        m.nowline.visible = false
    end if

    if m.chans.count() = 0
        m.rows.appendChild(mkLabel(0, 20, m.CHAN_W + m.GRID_W, 60, "No channels here yet.  Press * on a channel anywhere to add it to Favorites; tick groups in the Pi's Settings page.", "#AAB4BE", "font:MediumSystemFont"))
        m.top.detail = ""
        return
    end if

    lastRow = m.firstRow + m.VISIBLE - 1
    if lastRow > m.chans.count() - 1 then lastRow = m.chans.count() - 1
    focused = m.top.hasFocus()
    for r = m.firstRow to lastRow
        ch = m.chans[r]
        y = (r - m.firstRow) * (m.ROW_H + m.GAP)
        isRow = (r = m.top.focusRow)
        chBg = "#1C222A"
        if isRow then chBg = "#2A3340"
        m.rows.appendChild(mkRect(0, y, m.CHAN_W - 6, m.ROW_H, chBg))
        logoW = 0
        if ch.logo <> invalid and ch.logo <> ""
            lg = CreateObject("roSGNode", "Poster")
            lg.translation = [10, y + 11]
            lg.width = 52
            lg.height = 52
            lg.loadDisplayMode = "scaleToFit"
            lg.loadWidth = 52
            lg.loadHeight = 52
            lg.uri = ch.logo
            m.rows.appendChild(lg)
            logoW = 62
        end if
        star = ""
        if ch.favorite = 1 then star = "* "
        num = ""
        if ch.num <> invalid then num = ch.num.toStr() + "  "
        m.rows.appendChild(mkLabel(12 + logoW, y, m.CHAN_W - 30 - logoW, m.ROW_H \ 2 + 6, star + num, "#FFD166", "font:SmallSystemFont"))
        m.rows.appendChild(mkLabel(12 + logoW, y + m.ROW_H \ 2 - 6, m.CHAN_W - 30 - logoW, m.ROW_H \ 2, ch.name, "#FFFFFF", "font:SmallBoldSystemFont"))

        progs = ch.programs
        if progs = invalid or progs.count() = 0
            col = "#232A33"
            if isRow and focused then col = "#3D6BFF"
            m.rows.appendChild(mkRect(m.CHAN_W, y, m.GRID_W, m.ROW_H, col))
            m.rows.appendChild(mkLabel(m.CHAN_W + 14, y, m.GRID_W - 28, m.ROW_H, "No guide data  -  OK to watch", "#9AA4AE", "font:SmallSystemFont"))
        else
            for p = 0 to progs.count() - 1
                pr = progs[p]
                s = pr.start
                e = pr["stop"]
                if s < d.start then s = d.start
                if e > d["end"] then e = d["end"]
                if e > s
                    x = m.CHAN_W + int((s - d.start) / 60 * ppm)
                    w = int((e - s) / 60 * ppm) - 3
                    if w < 4 then w = 4
                    col = "#2E3742"
                    if pr.scheduled = true then col = "#7A2E3A"
                    if isRow and p = m.focusCol and focused then col = "#3D6BFF"
                    m.rows.appendChild(mkRect(x, y, w, m.ROW_H, col))
                    title = pr.title
                    if title = invalid then title = ""
                    if pr.scheduled = true then title = "[REC] " + title
                    if w > 40 then m.rows.appendChild(mkLabel(x + 10, y, w - 20, m.ROW_H, title, "#FFFFFF", "font:SmallSystemFont"))
                end if
            end for
        end if
    end for
    updateDetail()
end sub

function fmtDate(t as integer) as string
    dt = CreateObject("roDateTime")
    dt.FromSeconds(t)
    dt.ToLocalTime()
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    return days[dt.GetDayOfWeek()] + " " + dt.GetMonth().toStr() + "/" + dt.GetDayOfMonth().toStr()
end function

sub updateDetail()
    if m.top.focusRow >= m.chans.count()
        m.top.detail = ""
        return
    end if
    ch = m.chans[m.top.focusRow]
    progs = ch.programs
    if progs = invalid or progs.count() = 0 or m.focusCol >= progs.count()
        m.top.detail = ch.name
        return
    end if
    pr = progs[m.focusCol]
    txt = ch.name + "  |  " + fmtTime(pr.start) + " - " + fmtTime(pr["stop"]) + "   " + pr.title
    if pr.scheduled = true then txt = txt + "   (recording scheduled)"
    if pr.description <> invalid and pr.description <> "" then txt = txt + Chr(10) + pr.description
    m.top.detail = txt
end sub

function onKeyEvent(key as string, press as boolean) as boolean
    if not press then return false
    n = m.chans.count()
    if key = "back"
        m.top.goBack = true
        return true
    end if
    if n = 0
        if key = "left"
            m.top.goBack = true
            return true
        end if
        return false
    end if
    if key = "down"
        if m.top.focusRow < n - 1
            m.top.focusRow = m.top.focusRow + 1
            m.focusCol = colAtNow(m.top.focusRow)
            clampScroll()
            render()
        end if
        return true
    else if key = "up"
        if m.top.focusRow > 0
            m.top.focusRow = m.top.focusRow - 1
            m.focusCol = colAtNow(m.top.focusRow)
            clampScroll()
            render()
            return true
        end if
        ' at the top row let Up bubble so the scene can focus the overlay menu bar
        return false
    else if key = "right"
        progs = m.chans[m.top.focusRow].programs
        if progs <> invalid and m.focusCol < progs.count() - 1
            m.focusCol = m.focusCol + 1
            render()
        else
            m.top.pageTime = 1
        end if
        return true
    else if key = "left"
        if m.focusCol > 0
            m.focusCol = m.focusCol - 1
            render()
        else if m.top.data.now <> invalid and m.top.data.start > m.top.data.now - 1800
            m.top.goBack = true
        else
            m.top.pageTime = -1
        end if
        return true
    else if key = "fastforward"
        m.top.pageTime = 1
        return true
    else if key = "rewind"
        m.top.pageTime = -1
        return true
    else if key = "OK"
        ch = m.chans[m.top.focusRow]
        pr = invalid
        if ch.programs <> invalid and m.focusCol < ch.programs.count() then pr = ch.programs[m.focusCol]
        m.top.selected = {channel: ch, program: pr}
        return true
    else if key = "options"
        m.top.favToggle = m.chans[m.top.focusRow]
        return true
    end if
    return false
end function
