using System;
using System.Collections.Generic;
using System.Linq;

// Pure connection policy; caller serializes access. Device identifiers stay
// internal and are never emitted in JSON events or persisted to a log.
internal sealed class PeerPinning
{
    internal string SelectedPeerId {get;private set;}
    internal bool Blocked {get;private set;}
    internal string ErrorCode {get;private set;}

    internal void Observe(IEnumerable<string> peerIds)
    {
        if(Blocked) return;
        var all=peerIds.ToArray();
        if(all.Any(String.IsNullOrWhiteSpace)) {Block("PEER_ID_UNAVAILABLE");return;}
        var peers=all.Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        if(SelectedPeerId==null&&peers.Length==1) SelectedPeerId=peers[0];
        if(peers.Length>1||peers.Any(id=>!MatchesSelected(id))) Block("MULTIPLE_BLUETOOTH_PEERS");
    }

    internal bool MatchesSelected(string id)
    {
        return SelectedPeerId!=null&&String.Equals(SelectedPeerId,id,StringComparison.OrdinalIgnoreCase);
    }

    void Block(string code) {Blocked=true;ErrorCode=code;}
}
