// Independent, opt-in HOGP experiment. No driver, pairing API, hooks, or GUI.
// stdin/stdout are UTF-8 JSON Lines. Production integration must own the process
// and close stdin on every disconnect/exit. Hardware and iPhone validation differ.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Devices.Enumeration;
using Windows.Foundation;
using Windows.Storage.Streams;

internal static class HidExperiment
{
    static readonly object OutputLock = new object();
    static readonly object StateLock = new object();
    static readonly object PeerLock = new object();
    static readonly PeerPinning Pinning = new PeerPinning();
    static bool PeerErrorReported;
    static string SelectedPeerName;
    static GattServiceProvider Provider;
    static GattLocalCharacteristic Mouse;
    static GattLocalCharacteristic Keyboard;
    static byte[] MouseValue = new byte[4];
    static byte[] KeyboardValue = new byte[8];
    static volatile bool Suspended;
    static int CleaningUp;
    static bool WasConnected;
    static bool AdvertisingCalled;
    static bool Diagnostic;
    static long MouseNotifications;
    static long KeyboardNotifications;
    static long InputCommandCount,InputCommandTicks,MaxInputCommandTicks;
    static long NotifyAttemptCount,NotifyWaitTicks,MaxNotifyWaitTicks,NotifyFailureCount,NotifyTimeoutCount;
    static readonly NotifyTransport Transport=new NotifyTransport();
    static int TransportErrorReported;
    const int NotifyTimeoutMs = 1000;

    sealed class CommandTiming
    {
        internal long StartedAt;
        internal long NotifyTicks;
        internal int NotifyCount;
        internal bool OrdinaryInput;
    }
    static double Milliseconds(long ticks) {return Math.Round(ticks*1000.0/Stopwatch.Frequency,4);}
    static void Maximum(ref long target,long value) {
        long previous=Interlocked.Read(ref target);
        while(value>previous) {long observed=Interlocked.CompareExchange(ref target,value,previous);if(observed==previous) return;previous=observed;}
    }
    static object Performance() {
        long commands=Interlocked.Read(ref InputCommandCount),notifies=Interlocked.Read(ref NotifyAttemptCount);
        return Map("awaitMode","winrt-completed-callback","inputCommands",commands,
            "inputProcessingMeanMs",commands==0?0:Milliseconds(Interlocked.Read(ref InputCommandTicks))/commands,
            "inputProcessingMaxMs",Milliseconds(Interlocked.Read(ref MaxInputCommandTicks)),"notifyAttempts",notifies,
            "notifyMeanMs",notifies==0?0:Milliseconds(Interlocked.Read(ref NotifyWaitTicks))/notifies,
            "notifyMaxMs",Milliseconds(Interlocked.Read(ref MaxNotifyWaitTicks)),"notifyFailures",Interlocked.Read(ref NotifyFailureCount),
            "notifyTimeouts",Interlocked.Read(ref NotifyTimeoutCount),"transportTerminal",Transport.IsTerminal);
    }

    sealed class PeerState
    {
        internal GattSubscribedClient MouseClient;
        internal GattSubscribedClient KeyboardClient;
        internal int MouseCount;
        internal int KeyboardCount;
        internal bool Blocked;
        internal string SelectedId;
    }

    // Report IDs live in 0x2908 Report Reference; GATT report values omit IDs.
    static readonly byte[] ReportMap = new byte[] {
        0x05,0x01,0x09,0x02,0xA1,0x01,0x85,0x01,0x09,0x01,0xA1,0x00,
        0x05,0x09,0x19,0x01,0x29,0x03,0x15,0x00,0x25,0x01,0x95,0x03,
        0x75,0x01,0x81,0x02,0x95,0x01,0x75,0x05,0x81,0x03,0x05,0x01,
        0x09,0x30,0x09,0x31,0x09,0x38,0x15,0x81,0x25,0x7F,0x75,0x08,
        0x95,0x03,0x81,0x06,0xC0,0xC0,
        0x05,0x01,0x09,0x06,0xA1,0x01,0x85,0x02,0x05,0x07,0x19,0xE0,
        0x29,0xE7,0x15,0x00,0x25,0x01,0x75,0x01,0x95,0x08,0x81,0x02,
        0x95,0x01,0x75,0x08,0x81,0x03,0x95,0x06,0x75,0x08,0x15,0x00,
        0x26,0xFF,0x00,0x05,0x07,0x19,0x00,0x2A,0xFF,0x00,0x81,0x00,
        0xC0
    };
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr token, int cls, out int value, int length, out int returned);
    static Dictionary<string, object> Map(params object[] entries)
    {
        var d = new Dictionary<string, object>();
        for (int i=0; i<entries.Length; i+=2) d[(string)entries[i]] = entries[i+1];
        return d;
    }
    static void Emit(string type, params object[] entries)
    {
        var d=Map(entries); d["type"]=type; d["time"]=DateTimeOffset.Now.ToString("o");
        lock(OutputLock) { Console.WriteLine(new JavaScriptSerializer().Serialize(d)); Console.Out.Flush(); }
    }
    static void Fail(string stage, Exception e)
    {
        while(e.InnerException!=null) e=e.InnerException;
        Emit("error", "stage",stage,"hresult","0x"+e.HResult.ToString("X8"),"exception",e.GetType().FullName,"message",e.Message);
    }
    static void TransportFault(Exception error)
    {
        lock(StateLock) {MouseValue=new byte[4];KeyboardValue=new byte[8];}
        if(Interlocked.Exchange(ref TransportErrorReported,1)==0)
            Emit("error","stage","transport","code",error is TimeoutException?"NOTIFY_TIMEOUT_TERMINAL":"NOTIFY_TRANSPORT_TERMINAL",
                "message","Control stopped after a notification transport fault. Restart control to reconnect.",
                "hresult","0x"+error.HResult.ToString("X8"),"mouseSubscribed",false,"keyboardSubscribed",false,"transportTerminal",true);
    }
    static Guid Uuid(string shortId) { return new Guid("0000"+shortId+"-0000-1000-8000-00805f9b34fb"); }
    static IBuffer Buffer(byte[] bytes) { using(var writer=new DataWriter()) {writer.WriteBytes(bytes); return writer.DetachBuffer();} }
    static Task<T> Await<T>(IAsyncOperation<T> op,int timeout=15000) {return WinRtAwait.Wait(op,timeout);}
    static void Check(BluetoothError error, string stage)
    {
        if(error!=BluetoothError.Success) throw new InvalidOperationException(stage+": BluetoothError="+error+" ("+(int)error+")");
    }
    static async Task<GattLocalCharacteristic> Constant(string uuid, byte[] value)
    {
        var r=await Await(Provider.Service.CreateCharacteristicAsync(Uuid(uuid),new GattLocalCharacteristicParameters {
            CharacteristicProperties=GattCharacteristicProperties.Read,
            ReadProtectionLevel=GattProtectionLevel.EncryptionRequired,
            StaticValue=Buffer(value)
        }));
        Check(r.Error,"CreateCharacteristic "+uuid); return r.Characteristic;
    }
    static async Task<GattLocalCharacteristic> Input(byte reportId, int length)
    {
        var r=await Await(Provider.Service.CreateCharacteristicAsync(Uuid("2a4d"),new GattLocalCharacteristicParameters {
            CharacteristicProperties=GattCharacteristicProperties.Read|GattCharacteristicProperties.Notify,
            ReadProtectionLevel=GattProtectionLevel.EncryptionRequired,
            WriteProtectionLevel=GattProtectionLevel.EncryptionRequired
        }));
        Check(r.Error,"Create input report "+reportId);
        var characteristic=r.Characteristic;
        var descriptor=await Await(characteristic.CreateDescriptorAsync(Uuid("2908"),new GattLocalDescriptorParameters {
            ReadProtectionLevel=GattProtectionLevel.EncryptionRequired,
            StaticValue=Buffer(new byte[]{reportId,1})
        }));
        Check(descriptor.Error,"Create Report Reference "+reportId);
        characteristic.ReadRequested += async (sender,args) => {
            var deferral=args.GetDeferral();
            try {
                var request=await Await(args.GetRequestAsync());
                if(request==null) return;
                bool selected; lock(PeerLock) selected=!Pinning.Blocked&&Pinning.MatchesSelected(args.Session.DeviceId.Id);
                byte[] value; lock(StateLock) value=selected?(byte[])(reportId==1?MouseValue:KeyboardValue).Clone():new byte[length];
                if(request.Offset>value.Length) request.RespondWithProtocolError(0x07);
                else request.RespondWithValue(Buffer(value.Skip((int)request.Offset).ToArray()));
            } catch(Exception e) {Fail("read-report",e);} finally {deferral.Complete();}
        };
        characteristic.SubscribedClientsChanged += async (sender,args) => {
            try { await SubscriptionState(); } catch(Exception e) {Fail("subscriptions",e);}
        };
        return characteristic;
    }
    static string ClientId(GattSubscribedClient client)
    {
        try {return client.Session.DeviceId.Id;} catch {return null;}
    }
    static PeerState GetPeerState()
    {
        var mouse=Mouse==null?new GattSubscribedClient[0]:Mouse.SubscribedClients.ToArray();
        var keyboard=Keyboard==null?new GattSubscribedClient[0]:Keyboard.SubscribedClients.ToArray();
        var all=mouse.Concat(keyboard).ToArray();
        var ids=all.Select(ClientId).ToArray();
        var state=new PeerState {MouseCount=mouse.Length,KeyboardCount=keyboard.Length};
        string error=null;
        lock(PeerLock) {
            Pinning.Observe(ids);
            state.Blocked=Pinning.Blocked;state.SelectedId=Pinning.SelectedPeerId;
            state.MouseClient=mouse.FirstOrDefault(c=>Pinning.MatchesSelected(ClientId(c)));
            state.KeyboardClient=keyboard.FirstOrDefault(c=>Pinning.MatchesSelected(ClientId(c)));
            if(Pinning.Blocked&&!PeerErrorReported) {PeerErrorReported=true;error=Pinning.ErrorCode;}
        }
        if(error!=null) {
            lock(StateLock) {MouseValue=new byte[4];KeyboardValue=new byte[8];}
            Emit("error","stage","peer","code",error,"message","Input stopped: more than one Bluetooth peer or an unknown peer was observed. Restart control to choose a device.",
                "mouseSubscribed",false,"keyboardSubscribed",false,"peerName",SelectedPeerName);
        }
        return state;
    }
    static async Task SubscriptionState()
    {
        var state=GetPeerState();
        bool mouse=state.MouseClient!=null&&!state.Blocked&&!Transport.IsTerminal;
        bool keyboard=state.KeyboardClient!=null&&!state.Blocked&&!Transport.IsTerminal;
        bool connected=mouse;
        Emit("subscribed","mouse",state.MouseCount,"keyboard",state.KeyboardCount,"mouseSubscribed",mouse,"keyboardSubscribed",keyboard,
            "peerBlocked",state.Blocked,"peerName",SelectedPeerName,"inputProtection","EncryptionRequired");
        bool changed;
        lock(StateLock) {
            changed=WasConnected!=connected; WasConnected=connected;
            if(!mouse) MouseValue=new byte[4];
            if(!keyboard) KeyboardValue=new byte[8];
        }
        if(changed) Emit(connected?"connected":"disconnected","mouse",state.MouseCount,"keyboard",state.KeyboardCount,"mouseSubscribed",mouse,"keyboardSubscribed",keyboard,
            "meaning",connected?"Mouse input characteristic is subscribed; actual iPhone input remains to be verified.":"Mouse input is not subscribed.");
        if(state.Blocked||state.SelectedId==null||(!mouse&&!keyboard)) return;
        var client=state.MouseClient??state.KeyboardClient;
        if(client!=null) {
            try {
                using(var device=await Await(BluetoothLEDevice.FromIdAsync(state.SelectedId))) {
                    if(device!=null) {
                        SelectedPeerName=device.Name;
                        Emit("peer","name",SelectedPeerName,"peerName",SelectedPeerName,"paired",device.DeviceInformation.Pairing.IsPaired,
                            "connectionStatus",device.ConnectionStatus.ToString(),"sessionStatus",client.Session.SessionStatus.ToString(),
                            "pinnedUntilRestart",true,"sameAsAirPlayVerified",false);
                    }
                }
            } catch(Exception e) {Fail("observe-peer",e);}
        }
    }
    static async Task Setup()
    {
        var identity=WindowsIdentity.GetCurrent();
        int appContainer,returned;
        if(GetTokenInformation(identity.Token,29,out appContainer,4,out returned)&&appContainer!=0)
            throw new InvalidOperationException("APP_CONTAINER_RESTRICTED: run from the normal signed-in desktop session.");
        var adapter=await Await(BluetoothAdapter.GetDefaultAsync());
        if(adapter==null) throw new InvalidOperationException("NO_BLUETOOTH_ADAPTER");
        var info=await Await(DeviceInformation.CreateFromIdAsync(adapter.DeviceId));
        var radio=await Await(adapter.GetRadioAsync());
        Emit("capability","user",identity.Name,"sessionId",Process.GetCurrentProcess().SessionId,
            "administrator",new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator),
            "name",info.Name,"peripheralRoleSupported",adapter.IsPeripheralRoleSupported,"radioState",radio.State.ToString());
        if(!adapter.IsPeripheralRoleSupported) throw new InvalidOperationException("PERIPHERAL_ROLE_UNSUPPORTED");
        if(radio.State.ToString()!="On") throw new InvalidOperationException("BLUETOOTH_RADIO_NOT_ON");
        var sr=await Await(GattServiceProvider.CreateAsync(Uuid("1812")));
        Check(sr.Error,"Create HID service"); Provider=sr.ServiceProvider;
        await Constant("2a4a",new byte[]{0x11,0x01,0x00,0x02});
        await Constant("2a4b",ReportMap);
        Mouse=await Input(1,4); Keyboard=await Input(2,8);
        var control=await Await(Provider.Service.CreateCharacteristicAsync(Uuid("2a4c"),new GattLocalCharacteristicParameters {
            CharacteristicProperties=GattCharacteristicProperties.WriteWithoutResponse,
            WriteProtectionLevel=GattProtectionLevel.EncryptionRequired
        }));
        Check(control.Error,"Create HID Control Point");
        control.Characteristic.WriteRequested += async (sender,args) => {
            var deferral=args.GetDeferral();
            try {
                var request=await Await(args.GetRequestAsync());
                if(request==null) return;
                using(var reader=DataReader.FromBuffer(request.Value)) {
                    if(reader.UnconsumedBufferLength>0) Suspended=reader.ReadByte()==0;
                }
                if(request.Option==GattWriteOption.WriteWithResponse) request.Respond();
                Emit("suspend","suspended",Suspended);
            } catch(Exception e) {Fail("control-point",e);} finally {deferral.Complete();}
        };
        Provider.AdvertisementStatusChanged += (sender,args) => Emit("advertisement","status",args.Status.ToString(),"error",args.Error.ToString(),"errorValue",(int)args.Error);
        Provider.StartAdvertising(new GattServiceProviderAdvertisingParameters {IsDiscoverable=true,IsConnectable=true});
        AdvertisingCalled=true;
        // The stack can report transient Aborted/Success before it reaches Started.
        await Task.Delay(1200);
        var status=Provider.AdvertisementStatus.ToString();
        if(status!="Started"&&status!="StartedWithoutAllAdvertisementData") throw new InvalidOperationException("ADVERTISEMENT_NOT_STARTED: "+status);
        var process=Process.GetCurrentProcess(); process.Refresh();
        Emit("ready","advertisementStatus",status,"name",info.Name,"adapterName",info.Name,"advertisedNameVerified",false,"computerName",Environment.MachineName,"serviceUuid",Uuid("1812").ToString(),
            "reportMapBytes",ReportMap.Length,"mouseReportId",1,"keyboardReportId",2,"inputVerified",false,
            "mouseSubscribed",false,"keyboardSubscribed",false,"inputAckSupported",true,"notifyTimeoutMs",NotifyTimeoutMs,
            "peerPinnedUntilRestart",true,"sameAsAirPlayVerified",false,
            "awaitMode","winrt-completed-callback","ackTimingSupported",true,
            "privateBytes",process.PrivateMemorySize64,"workingSetBytes",process.WorkingSet64);
        await SubscriptionState();
    }
    static int Number(Dictionary<string,object> command,string name,int fallback,int minimum,int maximum)
    {
        object value; if(!command.TryGetValue(name,out value)) return fallback;
        if(!(value is int)&&!(value is long)) throw new ArgumentException(name+" must be an integer.");
        long n=Convert.ToInt64(value);
        if(n<minimum||n>maximum) throw new ArgumentOutOfRangeException(name,"Allowed range "+minimum+".."+maximum);
        return (int)n;
    }
    static async Task<bool> Notify(GattLocalCharacteristic characteristic,byte[] bytes,string report,bool allowUnsubscribed=false,bool suppressErrors=false,CommandTiming timing=null)
    {
        if(Transport.IsTerminal) {ClearReport(report);return false;}
        var state=GetPeerState();
        var target=report=="mouse"?state.MouseClient:state.KeyboardClient;
        if(state.Blocked&&!allowUnsubscribed) {ClearReport(report);return false;}
        if(characteristic==null||target==null) {
            ClearReport(report);
            if(!allowUnsubscribed&&!suppressErrors) Emit("error","stage","input","code","NO_SUBSCRIBERS","report",report);
            return false;
        }
        GattClientNotificationResult result;
        long notifyStarted=Stopwatch.GetTimestamp();bool notifySucceeded=false;
        // Always select one client explicitly; never use the broadcast overload.
        // During a peer conflict only zero-state release to the pinned peer is allowed.
        try {result=await Transport.Send(()=>characteristic.NotifyValueAsync(Buffer(bytes),target),NotifyTimeoutMs);notifySucceeded=result.Status==GattCommunicationStatus.Success;}
        catch(Exception error) {
            if(error is TimeoutException) Interlocked.Increment(ref NotifyTimeoutCount);
            ClearReport(report);if(Transport.IsTerminal) TransportFault(error);throw;
        }
        finally {
            long elapsed=Stopwatch.GetTimestamp()-notifyStarted;
            Interlocked.Increment(ref NotifyAttemptCount);Interlocked.Add(ref NotifyWaitTicks,elapsed);Maximum(ref MaxNotifyWaitTicks,elapsed);
            if(!notifySucceeded) Interlocked.Increment(ref NotifyFailureCount);
            if(timing!=null) {timing.NotifyTicks+=elapsed;timing.NotifyCount++;}
        }
        bool success=notifySucceeded;
        var statuses=new string[]{result.Status.ToString()};
        if(!success) ClearReport(report);
        if(success) {if(report=="mouse") Interlocked.Increment(ref MouseNotifications);else Interlocked.Increment(ref KeyboardNotifications);}
        if(Diagnostic) Emit("reportSent","report",report,"success",success,"bytes",bytes.Select(b=>(int)b).ToArray(),"notificationStatuses",statuses);
        else if(!success&&!suppressErrors) Emit("error","stage","notify","report",report,"code","NOTIFY_FAILED","notificationStatuses",statuses);
        return success;
    }
    static void ClearReport(string report)
    {
        lock(StateLock) {if(report=="mouse") MouseValue=new byte[4];else KeyboardValue=new byte[8];}
    }
    static async Task Release(CommandTiming timing=null)
    {
        lock(StateLock) {MouseValue=new byte[4];KeyboardValue=new byte[8];}
        if(Transport.IsTerminal) return;
        var failures=new List<object>();
        try {
            var characteristics=new GattLocalCharacteristic[]{Mouse,Keyboard};
            var reports=new string[]{"mouse","key"};
            for(int i=0;i<characteristics.Length;i++) {
                if(Transport.IsTerminal) break;
                try {
                    var state=GetPeerState();
                    if(characteristics[i]==null||(i==0?state.MouseClient:state.KeyboardClient)==null) continue;
                    if(!await Notify(characteristics[i],new byte[i==0?4:8],reports[i],true,true,timing))
                        failures.Add(Map("report",reports[i],"code","NOTIFY_FAILED"));
                }
                catch(Exception e) {
                    if(Transport.IsTerminal) break;
                    while(e.InnerException!=null) e=e.InnerException;
                    failures.Add(Map("report",reports[i],"hresult","0x"+e.HResult.ToString("X8"),"exception",e.GetType().FullName,"message",e.Message));
                }
            }
        }
        finally {lock(StateLock) {MouseValue=new byte[4];KeyboardValue=new byte[8];}}
        // Release failures must not re-enter the caller's ordinary input-error
        // recovery path. One aggregate event per attempt, then keep processing.
        if(failures.Count>0) Emit("error","stage","release","code","RELEASE_INCOMPLETE","failures",failures);
    }
    static async Task<bool> Command(Dictionary<string,object> command,CommandTiming timing)
    {
        object value; if(!command.TryGetValue("cmd",out value)) command.TryGetValue("type",out value);
        string cmd=value as string;
        if(cmd=="quit") {await Cleanup("quit",timing);return false;}
        if(cmd=="release") {await Release(timing);Emit("released");return true;}
        if(cmd=="status") {
            await SubscriptionState();var p=Process.GetCurrentProcess();p.Refresh();
            Emit("status","advertisementStatus",Provider.AdvertisementStatus.ToString(),"suspended",Suspended,"privateBytes",p.PrivateMemorySize64,"workingSetBytes",p.WorkingSet64,
                "mouseNotifications",Interlocked.Read(ref MouseNotifications),"keyboardNotifications",Interlocked.Read(ref KeyboardNotifications),"performance",Performance());return true;
        }
        if(Suspended) throw new InvalidOperationException("HID_SUSPENDED: iPhone requested suspend.");
        if(cmd=="mouse") {
            var bytes=new byte[]{(byte)Number(command,"buttons",0,0,7),unchecked((byte)Number(command,"dx",0,-127,127)),unchecked((byte)Number(command,"dy",0,-127,127)),unchecked((byte)Number(command,"wheel",0,-127,127))};
            lock(StateLock) MouseValue=(byte[])bytes.Clone();await Notify(Mouse,bytes,"mouse",false,false,timing);return true;
        }
        if(cmd=="key") {
            var bytes=new byte[8];bytes[0]=(byte)Number(command,"mod",0,0,255);
            object keys; if(!command.TryGetValue("keys",out keys)) command.TryGetValue("key",out keys);
            if(keys!=null) {
                var array=keys as ArrayList;
                var list=array!=null?array.Cast<object>().ToArray():(keys is object[]?(object[])keys:new object[]{keys});
                if(list.Length>6) throw new ArgumentException("At most six keyboard usages per report.");
                for(int i=0;i<list.Length;i++) bytes[i+2]=(byte)Number(Map("key",list[i]),"key",0,0,255);
            }
            lock(StateLock) KeyboardValue=(byte[])bytes.Clone();await Notify(Keyboard,bytes,"key",false,false,timing);return true;
        }
        throw new ArgumentException("Unknown command. Supported: mouse, key, release, status, quit.");
    }
    static async Task Cleanup(string reason,CommandTiming timing=null)
    {
        if(Interlocked.Exchange(ref CleaningUp,1)!=0) return;
        if(Transport.StopIfBusy()) TransportFault(new NotifyTransportFaultException());
        try {await Release(timing);} catch(Exception e) {Fail("release",e);}
        if(Provider!=null&&AdvertisingCalled) {
            try {Provider.StopAdvertising();await Task.Delay(500);}
            catch(Exception e) {Fail("stop-advertising",e);}
        }
        Emit("stopped","reason",reason,"stopAdvertisingCalled",AdvertisingCalled,
            "advertisementStatus",Provider==null?"not-created":Provider.AdvertisementStatus.ToString(),"processExitReleasesService",true,"transportTerminal",Transport.IsTerminal,"performance",Performance());
    }
    internal static bool ProcessLine(string line)
    {
        long? sequence=null;
        long commandStarted=Stopwatch.GetTimestamp();CommandTiming timing=null;
        try {
            var command=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(line);
            if(command==null) throw new ArgumentException("A JSON object is required.");
            object commandName,sequenceValue;
            if(!command.TryGetValue("cmd",out commandName)) command.TryGetValue("type",out commandName);
            string name=commandName as string;
            if(name=="mouse"||name=="key"||name=="release"||name=="quit") timing=new CommandTiming {StartedAt=commandStarted,OrdinaryInput=name=="mouse"||name=="key"};
            if((name=="mouse"||name=="key"||name=="release"||name=="quit")&&command.TryGetValue("seq",out sequenceValue)) {
                if(!(sequenceValue is int)&&!(sequenceValue is long)) throw new ArgumentException("seq must be an integer.");
                sequence=Convert.ToInt64(sequenceValue);
            }
            return Command(command,timing).GetAwaiter().GetResult()&&!Transport.IsTerminal;
        } catch(Exception e) {if(Transport.IsTerminal) TransportFault(e);else Fail("command",e);return !Transport.IsTerminal;}
        finally {
            long elapsed=Stopwatch.GetTimestamp()-commandStarted;
            if(timing!=null&&timing.OrdinaryInput) {Interlocked.Increment(ref InputCommandCount);Interlocked.Add(ref InputCommandTicks,elapsed);Maximum(ref MaxInputCommandTicks,elapsed);}
            if(sequence.HasValue) Emit("inputAck","seq",sequence.Value,"elapsedMs",Milliseconds(elapsed),
                "notifyMs",timing==null?0:Milliseconds(timing.NotifyTicks),"notifyCount",timing==null?0:timing.NotifyCount);
        }
    }
    static int Main(string[] args)
    {
        Console.InputEncoding=new UTF8Encoding(false);Console.OutputEncoding=new UTF8Encoding(false);
        Console.CancelKeyPress += (sender,e) => {e.Cancel=true;Cleanup("ctrl-c").GetAwaiter().GetResult();Environment.Exit(0);};
        int exit=0;string reason="stdin-eof";int probeSeconds=0;
        try {
            for(int i=0;i<args.Length;i++) {
                if(args[i]=="--diagnostic") Diagnostic=true;
                else if(args[i]=="--probe"||args[i]=="--probe-seconds") {
                    probeSeconds=3;int parsed;
                    if(i+1<args.Length&&int.TryParse(args[i+1],out parsed)) {probeSeconds=parsed;i++;}
                    if(probeSeconds<1||probeSeconds>60) throw new ArgumentOutOfRangeException("probe-seconds");
                }
                else throw new ArgumentException("Unknown command-line option: "+args[i]);
            }
            Setup().GetAwaiter().GetResult();
            if(probeSeconds>0) {
                Thread.Sleep(probeSeconds*1000);reason="bounded-probe";
            } else {
                string line;
                while((line=Console.ReadLine())!=null) {
                    if(line.Length==0) continue;
                    if(line.Length>4096) {Emit("error","stage","command","code","LINE_TOO_LONG");continue;}
                    if(!ProcessLine(line)) {reason=Transport.IsTerminal?"transport-fault":"quit";if(Transport.IsTerminal) exit=3;break;}
                }
            }
        } catch(Exception e) {Fail("setup",e);exit=2;reason="setup-failed";}
        finally {Cleanup(reason).GetAwaiter().GetResult();}
        return exit;
    }
}
