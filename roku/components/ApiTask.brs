sub init()
    m.top.functionName = "run"
end sub

sub run()
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
    if code < 200 or code >= 300
        m.top.error = "HTTP " + code.toStr() + ": " + msg.getFailureReason()
        return
    end if
    parsed = ParseJson(msg.getString())
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
