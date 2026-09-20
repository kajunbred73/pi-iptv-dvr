sub init()
    m.top.functionName = "doRequest"
end sub

sub doRequest()
    xfer = CreateObject("roUrlTransfer")
    port = CreateObject("roMessagePort")
    xfer.setMessagePort(port)
    xfer.setUrl(m.top.url)
    xfer.retainBodyOnError(true)
    xfer.addHeader("Content-Type", "application/json")
    xfer.addHeader("Accept", "application/json")
    xfer.setRequest(m.top.method)

    ok = false
    if m.top.method = "GET"
        ok = xfer.asyncGetToString()
    else
        ok = xfer.asyncPostFromString(m.top.body)
    end if
    if not ok
        m.top.error = "request failed to start"
        return
    end if

    msg = wait(15000, port)
    if type(msg) <> "roUrlEvent"
        xfer.asyncCancel()
        m.top.error = "timeout contacting server"
        return
    end if
    code = msg.getResponseCode()
    parsed = ParseJson(msg.getString())
    if code < 200 or code >= 300
        ' Most endpoints answer failures with {"ok": false, "error": "..."}; pass that
        ' through so the real reason (e.g. provider stream limit) reaches the screen.
        if parsed <> invalid and type(parsed) = "roAssociativeArray"
            parsed.tag = m.top.tag
            m.top.response = parsed
        else
            m.top.error = "HTTP " + code.toStr() + ": " + msg.getFailureReason()
        end if
        return
    end if
    if parsed = invalid
        m.top.error = "bad JSON from server"
        return
    end if
    ' Wrap arrays so the field type stays an assocarray.
    if type(parsed) = "roArray"
        m.top.response = { items: parsed, tag: m.top.tag }
    else
        parsed.tag = m.top.tag
        m.top.response = parsed
    end if
end sub
