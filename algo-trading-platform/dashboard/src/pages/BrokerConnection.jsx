import { useState, useEffect } from 'react';
import {
  Link2, Unlink, CheckCircle, XCircle, ExternalLink, Lock, Key, Eye, EyeOff,
  RefreshCw, Loader, Wifi,
} from 'lucide-react';
import Card from '../components/common/Card';
import {
  useFyersStatus, useInitFyersConnect, useFyersConnectionStatus,
  useDisconnectFyers, useReconnectFyers,
} from '../hooks/useApi';

function SecretField({ label, value, onChange, placeholder }) {
  const [visible, setVisible] = useState(false);
  return (
    <div>
      <label className="block text-xs font-semibold text-slate-400 mb-1.5">{label}</label>
      <div className="flex items-start gap-2">
        <input
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="flex-1 bg-slate-800/60 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-white font-mono
            placeholder:text-slate-600 focus:outline-none focus:border-blue-500/60 transition-colors duration-150"
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="p-2 rounded-lg border border-slate-700/50 text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors duration-150 flex-shrink-0 mt-0.5"
          title={visible ? 'Hide' : 'Show'}
        >
          {visible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}

function SectionHeader({ icon: Icon, title, subtitle }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <div className="w-9 h-9 rounded-xl bg-accent/10 flex items-center justify-center">
        <Icon className="w-5 h-5 text-accent" />
      </div>
      <div>
        <h2 className="text-sm font-bold text-white">{title}</h2>
        {subtitle && <p className="text-[11px] text-slate-500">{subtitle}</p>}
      </div>
    </div>
  );
}

export default function BrokerConnection() {
  const [fyersAppId, setFyersAppId] = useState('');
  const [fyersSecretKey, setFyersSecretKey] = useState('');
  const [fyersBanner, setFyersBanner] = useState(null);
  const [isPolling, setIsPolling] = useState(false);

  const { data: fyersStatusData } = useFyersStatus();
  const fyersStatus = fyersStatusData || {};
  const initConnect = useInitFyersConnect();
  const { data: connStatusData } = useFyersConnectionStatus(isPolling);
  const connStatus = connStatusData || {};
  const disconnectFyers = useDisconnectFyers();
  const reconnectFyers = useReconnectFyers();

  useEffect(() => {
    if (connStatus.status === 'connected' || connStatus.status === 'error') {
      setIsPolling(false);
      if (connStatus.status === 'connected') {
        setFyersBanner({ type: 'success', msg: 'Fyers connected! Live data feed is active.' });
      } else if (connStatus.status === 'error') {
        setFyersBanner({ type: 'error', msg: connStatus.message || 'Connection failed' });
      }
    }
  }, [connStatus.status, connStatus.message]);

  const handleFyersConnect = async () => {
    setFyersBanner(null);
    try {
      const resp = await initConnect.mutateAsync({
        app_id: fyersAppId,
        secret_key: fyersSecretKey,
      });
      if (resp.auth_url) {
        setIsPolling(true);
        window.open(resp.auth_url, '_blank', 'noopener');
        setFyersBanner({ type: 'info', msg: 'Fyers login opened — complete authentication in the new tab.' });
      }
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to start connection';
      setFyersBanner({ type: 'error', msg: detail });
    }
  };

  const handleFyersDisconnect = async () => {
    setFyersBanner(null);
    try {
      await disconnectFyers.mutateAsync();
      setFyersBanner({ type: 'success', msg: 'Disconnected from Fyers.' });
    } catch (err) {
      setFyersBanner({ type: 'error', msg: err?.message || 'Disconnect failed' });
    }
  };

  const handleFyersReconnect = async () => {
    setFyersBanner(null);
    try {
      const res = await reconnectFyers.mutateAsync();
      if (res?.live_feed_connected) {
        setFyersBanner({ type: 'success', msg: 'Reconnected using stored token.' });
      } else {
        setFyersBanner({ type: 'error', msg: res?.message || 'Reconnect failed — token may have expired. Use Connect Fyers.' });
      }
    } catch (err) {
      setFyersBanner({ type: 'error', msg: err?.response?.data?.detail || err?.message || 'Reconnect failed' });
    }
  };

  const isLiveConnected = fyersStatus.live_feed_connected || connStatus.live_feed_connected;

  return (
    <div className="space-y-4 max-w-[1440px] mx-auto animate-fade-in">
      <div>
        <h1 className="text-xl font-bold text-white tracking-tight">Broker Connection</h1>
        <p className="text-xs text-slate-400 mt-0.5">Connect and manage your broker API for live market data</p>
      </div>

      {/* Connection Status Card */}
      <Card>
        <div className="flex items-center justify-between mb-5">
          <SectionHeader icon={Link2} title="Fyers Broker Connection" subtitle="Connect your Fyers account for live market data" />
          <div className="flex items-center gap-2">
            <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold tracking-wide border ${
              isLiveConnected
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25'
                : isPolling
                ? 'bg-amber-500/10 text-amber-400 border-amber-500/25'
                : 'bg-slate-500/10 text-slate-400 border-slate-500/25'
            }`}>
              <span className={`w-2 h-2 rounded-full ${
                isLiveConnected ? 'bg-emerald-400 animate-pulse' : isPolling ? 'bg-amber-400 animate-pulse' : 'bg-slate-500'
              }`} />
              {isLiveConnected ? 'CONNECTED' : isPolling ? 'AUTHENTICATING...' : 'DISCONNECTED'}
            </span>
          </div>
        </div>

        {fyersBanner && (
          <div className={`flex items-center gap-2.5 px-4 py-3 rounded-lg mb-4 text-sm font-medium ${
            fyersBanner.type === 'success' ? 'bg-emerald-500/10 border border-emerald-500/25 text-emerald-400'
            : fyersBanner.type === 'info' ? 'bg-blue-500/10 border border-blue-500/25 text-blue-400'
            : 'bg-red-500/10 border border-red-500/25 text-red-400'
          }`}>
            {fyersBanner.type === 'success' ? <CheckCircle className="w-4 h-4 flex-shrink-0" />
              : fyersBanner.type === 'info' ? <ExternalLink className="w-4 h-4 flex-shrink-0" />
              : <XCircle className="w-4 h-4 flex-shrink-0" />}
            {fyersBanner.msg}
          </div>
        )}

        {isLiveConnected ? (
          <div className="space-y-4">
            <div className="rounded-xl p-4" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.15)' }}>
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 rounded-xl bg-emerald-500/15 flex items-center justify-center">
                  <CheckCircle className="w-5 h-5 text-emerald-400" />
                </div>
                <div>
                  <div className="text-sm font-bold text-emerald-400">Fyers Live Feed Active</div>
                  <div className="text-[11px] text-slate-500">Real-time market data streaming via WebSocket + REST</div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 mt-3">
                <div className="rounded-lg bg-slate-800/40 px-3 py-2">
                  <div className="text-[10px] text-slate-500 uppercase tracking-wider">App ID</div>
                  <div className="text-sm font-mono text-white mt-0.5">{fyersStatus.app_id || '—'}</div>
                </div>
                <div className="rounded-lg bg-slate-800/40 px-3 py-2">
                  <div className="text-[10px] text-slate-500 uppercase tracking-wider">Token Status</div>
                  <div className="text-sm font-mono text-emerald-400 mt-0.5">
                    {fyersStatus.access_token_set ? 'Valid' : 'Missing'}
                  </div>
                </div>
              </div>
            </div>

            <div className="flex items-center justify-between pt-3 border-t border-terminal-border">
              <p className="text-[11px] text-slate-500">
                Reconnect retries with the saved token (no re-login). Disconnect clears it.
              </p>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleFyersReconnect}
                  disabled={reconnectFyers.isPending}
                  title="Retry the live feed using the stored token — use if the feed dropped but the token is still valid today"
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold
                    bg-accent/10 text-accent border border-accent/20
                    hover:bg-accent/20 active:scale-95 transition-all duration-150
                    disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <RefreshCw className={`w-4 h-4 ${reconnectFyers.isPending ? 'animate-spin' : ''}`} />
                  {reconnectFyers.isPending ? 'Reconnecting...' : 'Reconnect'}
                </button>
                <button
                  onClick={handleFyersDisconnect}
                  disabled={disconnectFyers.isPending}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold
                    bg-red-500/10 text-red-400 border border-red-500/20
                    hover:bg-red-500/20 active:scale-95 transition-all duration-150
                    disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Unlink className="w-4 h-4" />
                  {disconnectFyers.isPending ? 'Disconnecting...' : 'Disconnect'}
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            {fyersStatus.access_token_set && (
              <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-lg"
                style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)' }}>
                <div>
                  <div className="text-sm font-semibold text-amber-400">Feed dropped — a saved token is still on file</div>
                  <div className="text-[11px] text-slate-500">
                    Try Reconnect first (no re-login). If the token expired, use Connect Fyers below.
                  </div>
                </div>
                <button
                  onClick={handleFyersReconnect}
                  disabled={reconnectFyers.isPending}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold flex-shrink-0
                    bg-accent/10 text-accent border border-accent/20
                    hover:bg-accent/20 active:scale-95 transition-all duration-150
                    disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <RefreshCw className={`w-4 h-4 ${reconnectFyers.isPending ? 'animate-spin' : ''}`} />
                  {reconnectFyers.isPending ? 'Reconnecting...' : 'Reconnect'}
                </button>
              </div>
            )}

            <div className="flex items-start gap-2.5 px-3 py-3 rounded-lg text-xs"
              style={{ background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.15)' }}>
              <Key className="w-3.5 h-3.5 text-blue-400 mt-0.5 flex-shrink-0" />
              <span className="text-slate-400">
                Enter your Fyers API credentials below and click <span className="text-blue-400 font-semibold">Connect</span>.
                A Fyers login page will open where you can authenticate with your TOTP/OTP.
                The access token is fetched automatically — no manual copy-paste needed.
              </span>
            </div>

            <SecretField
              label="App ID"
              value={fyersAppId}
              onChange={setFyersAppId}
              placeholder="e.g. 2VCPOWXCZM-100"
            />
            <SecretField
              label="Secret Key"
              value={fyersSecretKey}
              onChange={setFyersSecretKey}
              placeholder="Your Fyers API secret key"
            />

            {isPolling && (
              <div className="flex items-center gap-3 px-4 py-3 rounded-lg"
                style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)' }}>
                <Loader className="w-4 h-4 text-amber-400 animate-spin" />
                <div>
                  <div className="text-sm font-medium text-amber-400">Waiting for authentication...</div>
                  <div className="text-[11px] text-slate-500">
                    {connStatus.message || 'Complete the login in the Fyers tab. This will update automatically.'}
                  </div>
                </div>
              </div>
            )}

            <div className="flex items-center justify-between pt-4 border-t border-terminal-border">
              <p className="text-[11px] text-slate-500">
                Opens Fyers login in a new tab. Token is saved automatically.
              </p>
              <button
                onClick={handleFyersConnect}
                disabled={initConnect.isPending || isPolling || !fyersAppId || !fyersSecretKey}
                className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold bg-accent text-white
                  disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 active:scale-95 transition-all duration-150"
              >
                {initConnect.isPending ? (
                  <>
                    <span className="w-3.5 h-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                    Starting...
                  </>
                ) : (
                  <>
                    <Link2 className="w-4 h-4" />
                    Connect to Fyers
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </Card>

      {/* Security Notes */}
      <Card>
        <SectionHeader icon={Lock} title="Security Notes" subtitle="Best practices for API credentials" />
        <ul className="space-y-2.5 text-sm text-slate-400">
          {[
            'Your access token is fetched via OAuth and stored locally in the .env file.',
            'Fyers access tokens expire daily — reconnect each trading day.',
            'The secret key is permanent; treat it like a password.',
            'All credentials stay on your local machine (localhost only).',
            'Enable IP whitelisting in your Fyers developer account for extra security.',
          ].map((tip) => (
            <li key={tip} className="flex items-start gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-slate-500 mt-1.5 flex-shrink-0" />
              {tip}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
