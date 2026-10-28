"""Keep AirPlay discovery on an active physical Windows LAN interface."""


def patch_mdns(text):
    marker = '#include "mdnsd.h"'
    assert text.count(marker) == 1
    text = text.replace(marker, marker + '\n#ifdef _WIN32\n#include <netioapi.h>\n#include <limits.h>\n#endif', 1)
    start = text.index('#ifdef _WIN32', text.index('static uint32_t mdns_get_default_ipv4(void)'))
    end = text.index('#else', start)
    assert 'GetAdaptersAddresses' in text[start:end] and 'Hyper-V' in text[start:end]
    windows = r'''#ifdef _WIN32
    ULONG buflen = 15000;
    PIP_ADAPTER_ADDRESSES addresses = NULL;
    DWORD result = ERROR_BUFFER_OVERFLOW;
    int best_rank = -1;
    ULONG best_metric = ULONG_MAX;

    /* Enumeration order can prefer a VPN/TUN interface. Select hardware LANs
       explicitly instead of guessing from an adapter's display name. */
    for (int attempt = 0; attempt < 3 && result == ERROR_BUFFER_OVERFLOW; attempt++) {
        free(addresses);
        addresses = (PIP_ADAPTER_ADDRESSES) malloc(buflen);
        if (!addresses) return 0;
        result = GetAdaptersAddresses(AF_INET, GAA_FLAG_INCLUDE_GATEWAYS,
                                      NULL, addresses, &buflen);
    }
    if (result == NO_ERROR) {
        for (PIP_ADAPTER_ADDRESSES adapter = addresses; adapter; adapter = adapter->Next) {
            if (adapter->OperStatus != IfOperStatusUp ||
                (adapter->IfType != IF_TYPE_ETHERNET_CSMACD && adapter->IfType != IF_TYPE_IEEE80211) ||
                (adapter->Flags & IP_ADAPTER_NO_MULTICAST)) continue;
            MIB_IF_ROW2 row;
            memset(&row, 0, sizeof(row));
            row.InterfaceLuid = adapter->Luid;
            if (GetIfEntry2(&row) != NO_ERROR ||
                !row.InterfaceAndOperStatusFlags.HardwareInterface ||
                row.InterfaceAndOperStatusFlags.FilterInterface ||
                row.InterfaceAndOperStatusFlags.NotMediaConnected) continue;

            for (PIP_ADAPTER_UNICAST_ADDRESS unicast = adapter->FirstUnicastAddress;
                 unicast; unicast = unicast->Next) {
                if (!unicast->Address.lpSockaddr ||
                    unicast->Address.iSockaddrLength < (int) sizeof(struct sockaddr_in) ||
                    unicast->Address.lpSockaddr->sa_family != AF_INET ||
                    unicast->DadState != IpDadStatePreferred) continue;
                const struct sockaddr_in *local = (const struct sockaddr_in *) unicast->Address.lpSockaddr;
                uint32_t host = ntohl(local->sin_addr.s_addr);
                if ((host >> 24) == 0 || (host >> 24) == 127 || host >= 0xe0000000U ||
                    (host & 0xfffe0000U) == 0xc6120000U) continue;
                /* Keep physical link-local LANs usable, but prefer a normal
                   address and a gateway when several hardware LANs are up. */
                int rank = (adapter->FirstGatewayAddress ? 2 : 0) +
                           ((host & 0xffff0000U) != 0xa9fe0000U ? 1 : 0);
                if (rank > best_rank || (rank == best_rank && adapter->Ipv4Metric < best_metric)) {
                    addr = local->sin_addr.s_addr;
                    best_rank = rank;
                    best_metric = adapter->Ipv4Metric;
                }
            }
        }
    }
    free(addresses);
'''
    text = text[:start] + windows + text[end:]
    old = '''        setsockopt(fd, IPPROTO_IP, IP_MULTICAST_IF, (const char *) &iface, sizeof(iface));'''
    new = '''        if (setsockopt(fd, IPPROTO_IP, IP_MULTICAST_IF, (const char *) &iface, sizeof(iface)) == -1) {
            int error = SOCKET_GET_ERROR();
            CLOSESOCKET(fd);
            return -error;
        }'''
    assert text.count(old) == 1
    text = text.replace(old, new, 1)
    old = '''        if (iface_addr) {
            mreq.imr_interface.s_addr = htonl(INADDR_ANY);
            if (setsockopt(fd, IPPROTO_IP, IP_ADD_MEMBERSHIP,
                           (const char *) &mreq, sizeof(mreq)) == 0) {
                return fd;
            }
        }
'''
    assert text.count(old) == 1
    text = text.replace(old, '', 1)
    old = '''    mdnsd->ipv4_addr = mdns_get_default_ipv4();'''
    new = old + '''
#ifdef _WIN32
    if (!mdnsd->ipv4_addr) {
        puts("JJ_AIRPLAY_NETWORK_UNAVAILABLE");
        MUTEX_UNLOCK(mdnsd->mutex);
        return -WSAENETUNREACH;
    }
#endif'''
    assert text.count(old) == 1
    return text.replace(old, new, 1)
