using System;
using System.Threading;
using System.Threading.Tasks;
using Windows.Foundation;

// Direct WinRT completion bridge. This deliberately avoids both a polling
// interval and the Windows.winmd identity dependency of the AsTask extension.
internal static class WinRtAwait
{
    internal static async Task<T> Wait<T>(IAsyncOperation<T> operation,int timeout)
    {
        if(operation.Status!=AsyncStatus.Started) {
            try {return operation.GetResults();} finally {operation.Close();}
        }
        var source=new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        int winner=0,closed=0;
        Action close=()=>{if(Interlocked.Exchange(ref closed,1)==0) {try {operation.Close();} catch {}}};
        Timer timer=null;
        try {
            operation.Completed=(completed,status)=>{
                if(Interlocked.CompareExchange(ref winner,1,0)!=0) {close();return;}
                T value=default(T);Exception failure=null;bool canceled=status==AsyncStatus.Canceled;
                try {if(!canceled) value=completed.GetResults();} catch(Exception error) {failure=error;}
                finally {close();}
                if(failure!=null) source.TrySetException(failure);
                else if(canceled) source.TrySetCanceled();
                else source.TrySetResult(value);
            };
            if(!source.Task.IsCompleted) timer=new Timer(_=>{
                if(Interlocked.CompareExchange(ref winner,1,0)!=0) return;
                try {operation.Cancel();} catch {}
                source.TrySetException(new TimeoutException("WinRT operation timeout."));
            },null,timeout,Timeout.Infinite);
            return await source.Task.ConfigureAwait(false);
        }
        catch {
            // If callback registration itself fails, do not leave the owned
            // operation running. Timeout cancellation may complete later.
            if(Interlocked.CompareExchange(ref winner,1,0)==0) {try {operation.Cancel();} catch {}}
            throw;
        }
        finally {if(timer!=null) timer.Dispose();}
    }
}
