using System;
using System.Threading.Tasks;
using Windows.Foundation;

internal sealed class NotifyTransportFaultException:InvalidOperationException
{
    internal NotifyTransportFaultException():base("Bluetooth notification transport is terminal for this session.") {}
}

// The only entry point for a real notification factory. A timeout does not
// prove WinRT cancellation completed, so the process must never start another
// notification after that boundary, including a nominal zero-state release.
internal sealed class NotifyTransport
{
    readonly object Gate=new object();
    bool Terminal;
    bool InFlight;
    internal bool IsTerminal {get{lock(Gate) return Terminal;}}

    internal bool StopIfBusy() {
        lock(Gate) {if(InFlight) Terminal=true;return Terminal;}
    }

    internal async Task<T> Send<T>(Func<IAsyncOperation<T>> factory,int timeout)
    {
        IAsyncOperation<T> operation;
        lock(Gate) {
            if(Terminal) throw new NotifyTransportFaultException();
            if(InFlight) {Terminal=true;throw new NotifyTransportFaultException();}
            InFlight=true;
            try {operation=factory();}
            catch(TimeoutException) {Terminal=true;InFlight=false;throw;}
            catch {InFlight=false;throw;}
        }
        try {return await WinRtAwait.Wait(operation,timeout).ConfigureAwait(false);}
        // A failed Completed registration or another await-path exception can
        // also leave cancellation pending. Conservatively terminate every
        // failed await after the factory returned an operation.
        catch {lock(Gate) Terminal=true;throw;}
        finally {lock(Gate) InFlight=false;}
    }
}
