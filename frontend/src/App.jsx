import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  BadgeCheck,
  Clipboard,
  Clock,
  Coins,
  ExternalLink,
  Loader2,
  Plus,
  RefreshCw,
  Shield,
  Sparkles,
  Wallet,
  XCircle
} from 'lucide-react';
import {
  ENV_CONTRACT_ADDRESS,
  deadlineFromOffset,
  formatUnix,
  formatWeiToGen,
  formatWriteError,
  isContractConfigured,
  parseGenToWei,
  readContractState,
  sanitizeGenInput,
  sendContractTransaction,
  studionet,
  switchToGenlayerStudionet,
  toPercentInt,
  toUnixString,
  toWeiString,
  unixNowSeconds,
  waitForFinalizedTx
} from './genlayerClient';

const SESSION_KEY = 'authenticheck.contractAddress';
const AMOUNT_CHIPS = ['0.5', '1', '2', '5', '10'];
const DEADLINE_OPTIONS = [
  { seconds: '86400', label: '24 hours' },
  { seconds: '259200', label: '3 days' },
  { seconds: '604800', label: '7 days' }
];

const CATEGORIES = [
  {
    id: 'Sneakers',
    label: 'Sneakers',
    sources: ['StockX Verify', 'GOAT Authentication']
  },
  {
    id: 'Watch',
    label: 'Watch',
    sources: ['Chrono24', 'Watchfinder serial check']
  },
  {
    id: 'Trading Card',
    label: 'Trading card',
    sources: ['PSA Cert Lookup', 'BGS Cert Lookup']
  },
  {
    id: 'Handbag',
    label: 'Handbag',
    sources: ['Entrupy', 'The RealReal Authentication']
  },
  {
    id: 'Other',
    label: 'Other',
    sources: ['Official serial lookup', 'Public authentication page']
  }
];

const STATUS_META = {
  PENDING_PROOF: { label: 'Awaiting proof', tone: 'open' },
  SUBMITTED: { label: 'Proof submitted', tone: 'pending' },
  DISPUTED: { label: 'Needs clearer proof', tone: 'warn' },
  RESOLVED_AUTHENTIC: { label: 'Authentic — paid seller', tone: 'ok' },
  RESOLVED_FAKE: { label: 'Fake — refunded buyer', tone: 'bad' },
  PAYOUT_FAILED: { label: 'Payout failed', tone: 'bad' },
  EXPIRED_REFUNDED: { label: 'Expired — refunded buyer', tone: 'muted' }
};

const canonAddr = (a) => String(a || '').trim().toLowerCase().replace(/^0x/, '');
const sameAddress = (a, b) => {
  const left = canonAddr(a);
  const right = canonAddr(b);
  return left.length >= 40 && right.length >= 40 && left.slice(-40) === right.slice(-40);
};
const shortAddr = (addr) => {
  const s = String(addr || '');
  if (s.length < 12) return s || '—';
  return `${s.slice(0, 6)}…${s.slice(-4)}`;
};
const isHttp = (url) => {
  const v = String(url || '').trim().toLowerCase();
  return v.startsWith('http://') || v.startsWith('https://');
};

const toStringList = (val) => {
  if (Array.isArray(val)) return val.map(String);
  if (val && typeof val === 'object') {
    const keys = Object.keys(val);
    if (keys.length > 0 && keys.every((k) => /^\d+$/.test(k))) {
      return keys.sort((a, b) => (BigInt(a) < BigInt(b) ? -1 : 1)).map((k) => String(val[k]));
    }
  }
  return [];
};

const parseMaybeJson = (raw) => {
  if (raw == null) return null;
  if (typeof raw === 'string') {
    const s = raw.trim();
    if (!s) return null;
    try { return JSON.parse(s); } catch { return null; }
  }
  return raw;
};

const normalizeTx = (raw, fallbackId = '') => {
  const row = parseMaybeJson(raw);
  if (!row || typeof row !== 'object') return null;
  if (!row.item_description && !row.tx_id && !fallbackId) return null;
  return {
    tx_id: String(row.tx_id ?? fallbackId),
    buyer: String(row.buyer || ''),
    seller: String(row.seller || ''),
    item_category: String(row.item_category || ''),
    item_description: String(row.item_description || ''),
    serial_or_identifier: String(row.serial_or_identifier || ''),
    amount: toWeiString(row.amount),
    proof_deadline: toUnixString(row.proof_deadline),
    proof_urls: toStringList(row.proof_urls),
    reference_urls: toStringList(row.reference_urls),
    status: String(row.status || ''),
    verdict: String(row.verdict || ''),
    verdict_reason: String(row.verdict_reason || ''),
    confidence: toPercentInt(row.confidence),
    settled: Boolean(row.settled)
  };
};

const writeReachedAcceptedState = (functionName, args, before, after) => {
  if (functionName === 'create_transaction') return after.length > before.length;
  const id = String(args[0] ?? '');
  const prev = before.find((t) => t.tx_id === id);
  const next = after.find((t) => t.tx_id === id);
  if (!next) return false;
  if (functionName === 'submit_proof') return next.status === 'SUBMITTED';
  if (functionName === 'resolve_transaction') return next.status !== 'SUBMITTED';
  if (functionName === 'claim_expired_refund') return next.status !== 'PENDING_PROOF';
  if (functionName === 'retry_resolution') return next.status !== 'PAYOUT_FAILED';
  return next.status !== prev?.status;
};

const categoryLabel = (id) => CATEGORIES.find((c) => c.id === id)?.label || id || '—';

export default function App() {
  const [account, setAccount] = useState('');
  const [contractAddress, setContractAddress] = useState(() => {
    if (ENV_CONTRACT_ADDRESS) return ENV_CONTRACT_ADDRESS;
    try { return sessionStorage.getItem(SESSION_KEY) || ''; } catch { return ''; }
  });
  const [addressDraft, setAddressDraft] = useState('');
  const [tab, setTab] = useState('create');
  const [txs, setTxs] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);

  const [seller, setSeller] = useState('');
  const [category, setCategory] = useState('Sneakers');
  const [description, setDescription] = useState('');
  const [serial, setSerial] = useState('');
  const [amountGen, setAmountGen] = useState('1');
  const [deadlineSeconds, setDeadlineSeconds] = useState('259200');

  const [proofUrls, setProofUrls] = useState(['']);
  const [refUrls, setRefUrls] = useState(['', '']);

  const configured = isContractConfigured(contractAddress);
  const selected = useMemo(
    () => txs.find((t) => t.tx_id === selectedId) || null,
    [txs, selectedId]
  );
  const activeCategory = CATEGORIES.find((c) => c.id === category) || CATEGORIES[0];

  const connectWallet = async () => {
    if (!window.ethereum) {
      setToast({ kind: 'error', text: 'Install MetaMask to sign transactions on Studionet.' });
      return '';
    }
    await switchToGenlayerStudionet();
    const accs = await window.ethereum.request({ method: 'eth_requestAccounts' });
    const next = accs?.[0] || '';
    setAccount(next);
    return next;
  };

  const loadTxs = useCallback(async () => {
    if (!isContractConfigured(contractAddress)) {
      setTxs([]);
      return [];
    }
    setLoading(true);
    try {
      const countRaw = await readContractState('get_tx_count', [], contractAddress);
      let count = 0n;
      const countStr = typeof countRaw === 'string'
        ? countRaw.replace(/"/g, '').trim()
        : String(countRaw ?? '0');
      if (/^\d+$/.test(countStr)) count = BigInt(countStr);
      if (count > 200n) count = 200n;

      const loaded = [];
      if (count > 0n) {
        for (let i = 0n; i < count; i += 1n) {
          const id = i.toString();
          const raw = await readContractState('get_transaction', [id], contractAddress);
          const normalized = normalizeTx(raw, id);
          if (normalized && normalized.item_description) loaded.push(normalized);
        }
      }

      if (loaded.length === 0) {
        const listed = parseMaybeJson(await readContractState('list_transactions', [], contractAddress));
        const list = Array.isArray(listed) ? listed : [];
        for (let idx = 0; idx < list.length; idx += 1) {
          const normalized = normalizeTx(list[idx], String(idx));
          if (normalized && normalized.item_description) loaded.push(normalized);
        }
      }

      setTxs(loaded);
      return loaded;
    } catch (err) {
      console.warn('loadTxs', err);
      setToast({ kind: 'error', text: 'Could not read transactions from the contract.' });
      return [];
    } finally {
      setLoading(false);
    }
  }, [contractAddress]);

  useEffect(() => { loadTxs(); }, [loadTxs]);

  useEffect(() => {
    if (!window.ethereum) return undefined;
    const onAcc = (accs) => setAccount(accs?.[0] || '');
    window.ethereum.request({ method: 'eth_accounts' }).then((accs) => setAccount(accs?.[0] || '')).catch(() => {});
    window.ethereum.on?.('accountsChanged', onAcc);
    return () => window.ethereum.removeListener?.('accountsChanged', onAcc);
  }, []);

  const pasteInto = async (setter) => {
    try {
      const text = await navigator.clipboard.readText();
      setter(String(text || '').trim());
    } catch {
      setToast({ kind: 'error', text: 'Could not read the clipboard. Paste manually (Ctrl+V).' });
    }
  };

  const applyAddress = () => {
    const next = addressDraft.trim();
    if (!isContractConfigured(next)) {
      setToast({ kind: 'error', text: 'Contract address must be 0x followed by 40 hex characters.' });
      return;
    }
    try { sessionStorage.setItem(SESSION_KEY, next); } catch { /* ignore */ }
    setContractAddress(next);
    setToast({ kind: 'ok', text: 'Contract address saved for this session.' });
  };

  const runWrite = async (title, functionName, args, valueWei = 0n) => {
    if (!configured) {
      throw new Error('No contract address yet. Deploy on Studio, then paste the address in the banner.');
    }
    let from = account;
    if (!from) from = await connectWallet();
    const before = txs.slice();
    setToast({ kind: 'pending', text: `${title}: waiting for the MetaMask signature…` });
    const hash = await sendContractTransaction({
      from,
      to: contractAddress,
      functionName,
      args,
      value: valueWei
    });
    const waiting = functionName === 'resolve_transaction'
      ? 'The AI is reading the proof and comparing independent authentication sources…'
      : `${title}: waiting for GenLayer ACCEPTED…`;
    setToast({ kind: 'pending', text: waiting });
    await waitForFinalizedTx(hash);
    setToast({ kind: 'pending', text: `${title}: confirming the accepted contract state…` });
    let loaded = [];
    let accepted = false;
    for (let attempt = 0; attempt < 16; attempt += 1) {
      loaded = await loadTxs();
      accepted = writeReachedAcceptedState(functionName, args, before, loaded);
      if (accepted) break;
      await new Promise((r) => setTimeout(r, 2000));
    }
    if (!accepted) {
      throw new Error('GenLayer did not accept a payout. If a page showed a login wall, submit public text links again. Otherwise open Studio and check the result.');
    }
    setToast({ kind: 'ok', text: `${title} succeeded (ACCEPTED).` });
    return hash;
  };

  const onCreate = async (e) => {
    e.preventDefault();
    const desc = description.trim();
    const sellerAddr = seller.trim();
    const wei = parseGenToWei(amountGen);
    if (!/^0x[0-9a-fA-F]{40}$/.test(sellerAddr)) {
      setToast({ kind: 'error', text: 'Seller address must be 0x followed by 40 hex characters.' });
      return;
    }
    if (account && sameAddress(account, sellerAddr)) {
      setToast({ kind: 'error', text: 'Buyer and seller cannot be the same address.' });
      return;
    }
    if (!desc) {
      setToast({ kind: 'error', text: 'Item description cannot be empty.' });
      return;
    }
    if (wei <= 0n) {
      setToast({ kind: 'error', text: 'Escrow must be greater than 0 GEN.' });
      return;
    }
    try {
      const deadline = deadlineFromOffset(deadlineSeconds);
      await runWrite(
        'Create transaction',
        'create_transaction',
        [sellerAddr, category, desc, serial.trim(), deadline],
        wei
      );
      setDescription('');
      setSerial('');
      setTab('list');
    } catch (err) {
      setToast({ kind: 'error', text: formatWriteError(err) });
    }
  };

  const cleanUrls = (list) => list.map((u) => String(u || '').trim()).filter((u) => u.length > 0);

  const onSubmitProof = async (row) => {
    const proofs = cleanUrls(proofUrls);
    const refs = cleanUrls(refUrls);
    if (proofs.length < 1 || proofs.some((u) => !isHttp(u))) {
      setToast({ kind: 'error', text: 'At least 1 photo or video link is required (http/https).' });
      return;
    }
    if (refs.length < 2 || refs.some((u) => !isHttp(u))) {
      setToast({ kind: 'error', text: 'At least 2 independent authentication links are required (http/https).' });
      return;
    }
    try {
      await runWrite('Submit proof', 'submit_proof', [row.tx_id, proofs, refs]);
      setProofUrls(['']);
      setRefUrls(['', '']);
    } catch (err) {
      setToast({ kind: 'error', text: formatWriteError(err) });
    }
  };

  const onResolve = async (row) => {
    try {
      await runWrite('Request AI verification', 'resolve_transaction', [row.tx_id]);
    } catch (err) {
      setToast({ kind: 'error', text: formatWriteError(err) });
    }
  };

  const onRefund = async (row) => {
    try {
      await runWrite('Claim expired refund', 'claim_expired_refund', [row.tx_id]);
    } catch (err) {
      setToast({ kind: 'error', text: formatWriteError(err) });
    }
  };

  const onRetry = async (row) => {
    try {
      await runWrite('Retry payout', 'retry_resolution', [row.tx_id]);
    } catch (err) {
      setToast({ kind: 'error', text: formatWriteError(err) });
    }
  };

  const shareText = async (row) => {
    const text = `AuthentiCheck #${row.tx_id} — ${categoryLabel(row.item_category)} — ${formatWeiToGen(row.amount)} GEN. Seller must submit proof before ${formatUnix(row.proof_deadline)}.`;
    try {
      await navigator.clipboard.writeText(text);
      setToast({ kind: 'ok', text: `Copied the seller invite for transaction #${row.tx_id}.` });
    } catch {
      setToast({ kind: 'error', text: text });
    }
  };

  const deadlinePassed = (row) => {
    try {
      return unixNowSeconds() > BigInt(row.proof_deadline || '0');
    } catch {
      return false;
    }
  };

  const updateList = (list, index, value, setter) => {
    const next = list.slice();
    next[index] = value;
    setter(next);
  };

  return (
    <div className="app">
      <header className="nav">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true"><Shield size={22} /></div>
          <div>
            <h1>AuthentiCheck</h1>
            <p>Secondhand authenticity escrow on GenLayer</p>
          </div>
        </div>
        <div className="nav-actions">
          <span className="chip chip-net">Studionet · {String(studionet.id || 61999)}</span>
          <button className="btn btn-ghost" onClick={connectWallet}>
            <Wallet size={16} /> {account ? shortAddr(account) : 'Connect wallet'}
          </button>
        </div>
      </header>

      <div className="banner banner-free">
        <BadgeCheck size={18} />
        <div className="banner-body">
          <strong>Free to use. You only pay GenLayer network gas when you sign a transaction. There is no platform fee.</strong>
        </div>
      </div>

      {!configured && (
        <div className="banner banner-warn">
          <AlertTriangle size={18} />
          <div className="banner-body">
            <strong>No contract address yet.</strong>
            <p>The app still runs. Deploy on GenLayer Studio, confirm Result: SUCCESS, then paste the address here or set VITE_CONTRACT_ADDRESS. Write buttons stay locked until then, and the page does not go blank.</p>
            <div className="row">
              <input
                aria-label="Contract address"
                placeholder="0x… Studionet contract address"
                value={addressDraft}
                onChange={(e) => setAddressDraft(e.target.value.trim())}
              />
              <button className="btn btn-primary" type="button" onClick={applyAddress}>Save address</button>
            </div>
          </div>
        </div>
      )}

      {configured && (
        <div className="banner">
          <Shield size={18} />
          <div className="banner-body">
            <span>Studionet contract: {shortAddr(contractAddress)}</span>
            <a href={`https://explorer-studio.genlayer.com/address/${contractAddress}`} target="_blank" rel="noreferrer">
              Open explorer <ExternalLink size={14} />
            </a>
          </div>
        </div>
      )}

      {toast && (
        <div className={`toast toast-${toast.kind}`} role="status">
          {toast.kind === 'pending' && <Loader2 size={16} className="spin" />}
          {toast.kind === 'ok' && <BadgeCheck size={16} />}
          {toast.kind === 'error' && <XCircle size={16} />}
          <span>{toast.text}</span>
          <button className="toast-x" type="button" onClick={() => setToast(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      <div className="tabs">
        <button type="button" className={tab === 'create' ? 'on' : ''} onClick={() => setTab('create')}>Create transaction</button>
        <button type="button" className={tab === 'list' ? 'on' : ''} onClick={() => setTab('list')}>
          Transactions {loading ? '' : `(${txs.length})`}
        </button>
        <button type="button" className="btn btn-ghost" onClick={loadTxs} disabled={!configured || loading}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh
        </button>
      </div>

      <div className={tab === 'create' ? 'layout' : 'layout layout-single'}>
        {tab === 'create' && (
          <form className="panel" onSubmit={onCreate}>
            <h2>Buyer escrow</h2>
            <p className="muted">Pick a category, a short description, the GEN amount, and the seller’s proof deadline. The funds stay in the contract until the AI returns AUTHENTIC or FAKE.</p>

            <label className="field">
              Seller address
              <div className="row">
                <input
                  aria-label="Seller address"
                  placeholder="0x…"
                  value={seller}
                  onChange={(e) => setSeller(e.target.value.trim())}
                />
                <button className="btn btn-ghost" type="button" onClick={() => pasteInto(setSeller)}>
                  <Clipboard size={14} /> Paste
                </button>
              </div>
            </label>

            <div className="stack">
              <span className="tiny">Item category</span>
              <div className="chips">
                {CATEGORIES.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`chip ${category === item.id ? 'chip-on' : ''}`}
                    onClick={() => setCategory(item.id)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <p className="hint">Common independent sources for {activeCategory.label}: {activeCategory.sources.join(' · ')}. The seller submits real links from those sites, not a note they wrote themselves.</p>
            </div>

            <label className="field">
              Item description
              <textarea
                aria-label="Item description"
                maxLength={2000}
                placeholder="Example: Nike Dunk Low Panda, size 42, 2021 box"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>

            <label className="field">
              Serial or identifier (optional)
              <input
                aria-label="Serial"
                maxLength={200}
                placeholder="Leave blank if the item has none"
                value={serial}
                onChange={(e) => setSerial(e.target.value)}
              />
            </label>

            <div className="stack">
              <span className="tiny">Escrow amount (GEN)</span>
              <div className="chips">
                {AMOUNT_CHIPS.map((chip) => (
                  <button
                    key={chip}
                    type="button"
                    className={`chip ${amountGen === chip ? 'chip-on' : ''}`}
                    onClick={() => setAmountGen(chip)}
                  >
                    <Coins size={14} /> {chip}
                  </button>
                ))}
              </div>
              <input
                aria-label="GEN amount"
                inputMode="decimal"
                value={amountGen}
                onChange={(e) => setAmountGen(sanitizeGenInput(e.target.value))}
              />
            </div>

            <label className="field">
              Proof deadline
              <select
                aria-label="Proof deadline"
                value={deadlineSeconds}
                onChange={(e) => setDeadlineSeconds(e.target.value)}
              >
                {DEADLINE_OPTIONS.map((opt) => (
                  <option key={opt.seconds} value={opt.seconds}>{opt.label}</option>
                ))}
              </select>
            </label>

            <button className="btn btn-primary" type="submit" disabled={!configured || toast?.kind === 'pending'}>
              <Plus size={16} /> Create transaction and escrow
            </button>
            {!configured && <p className="tiny">Write actions stay locked until a contract address is set.</p>}
          </form>
        )}

        <section className="feed">
          {txs.length === 0 && (
            <div className="empty">
              <Shield size={28} />
              <p>{configured ? 'No transactions on this contract yet.' : 'The list stays empty until a contract address is set.'}</p>
            </div>
          )}

          {txs.map((row) => {
            const meta = STATUS_META[row.status] || { label: row.status, tone: 'muted' };
            return (
              <button
                key={row.tx_id}
                type="button"
                className={`card ${selectedId === row.tx_id ? 'on' : ''}`}
                onClick={() => { setSelectedId(row.tx_id); setTab('list'); }}
              >
                <div className="card-top">
                  <strong>#{row.tx_id} · {categoryLabel(row.item_category)}</strong>
                  <span className={`pill pill-${meta.tone}`}>{meta.label}</span>
                </div>
                <p className="desc">{row.item_description}</p>
                <div className="meta">
                  <span>{formatWeiToGen(row.amount)} GEN</span>
                  <span><Clock size={12} /> {formatUnix(row.proof_deadline)}</span>
                </div>
              </button>
            );
          })}

          {selected && (
            <TxDetail
              row={selected}
              account={account}
              configured={configured}
              pending={toast?.kind === 'pending'}
              proofUrls={proofUrls}
              refUrls={refUrls}
              setProofUrls={setProofUrls}
              setRefUrls={setRefUrls}
              updateList={updateList}
              pasteInto={pasteInto}
              onSubmitProof={onSubmitProof}
              onResolve={onResolve}
              onRefund={onRefund}
              onRetry={onRetry}
              shareText={shareText}
              deadlinePassed={deadlinePassed(selected)}
              sources={(CATEGORIES.find((c) => c.id === selected.item_category) || CATEGORIES[4]).sources}
            />
          )}
        </section>
      </div>
    </div>
  );
}

function UrlList({ label, urls, setUrls, pasteInto, updateList, addLabel }) {
  return (
    <div className="stack">
      <span className="tiny">{label}</span>
      {urls.map((url, index) => (
        <div className="url-row" key={`${label}-${index}`}>
          <input
            aria-label={`${label} ${index + 1}`}
            placeholder="https://"
            value={url}
            onChange={(e) => updateList(urls, index, e.target.value.trim(), setUrls)}
          />
          <button className="btn btn-ghost" type="button" onClick={() => pasteInto((text) => updateList(urls, index, text, setUrls))}>
            <Clipboard size={14} /> Paste
          </button>
        </div>
      ))}
      <button className="btn btn-ghost" type="button" onClick={() => setUrls(urls.concat(['']))}>{addLabel}</button>
    </div>
  );
}

function TxDetail({
  row, account, configured, pending, proofUrls, refUrls, setProofUrls, setRefUrls,
  updateList, pasteInto, onSubmitProof, onResolve, onRefund, onRetry, shareText,
  deadlinePassed, sources
}) {
  const meta = STATUS_META[row.status] || { label: row.status, tone: 'muted' };
  const isBuyer = account && sameAddress(account, row.buyer);
  const isSeller = account && sameAddress(account, row.seller);
  const canSubmit = isSeller && (
    row.status === 'DISPUTED'
    || row.status === 'SUBMITTED'
    || (row.status === 'PENDING_PROOF' && !deadlinePassed)
  );
  const canResolve = row.status === 'SUBMITTED' && (isBuyer || isSeller || !account);
  const canRefund = isBuyer && row.status === 'PENDING_PROOF' && deadlinePassed;
  const canRetry = (isBuyer || isSeller) && row.status === 'PAYOUT_FAILED';
  const verdictClass = row.verdict === 'AUTHENTIC' ? 'verdict-ok' : row.verdict === 'FAKE' ? 'verdict-no' : 'verdict-warn';

  return (
    <article className="panel detail">
      <div className="sheet-h">
        <div>
          <h2>Transaction #{row.tx_id}</h2>
          <p className="muted">{categoryLabel(row.item_category)} · {formatWeiToGen(row.amount)} GEN</p>
        </div>
        <span className={`pill pill-${meta.tone}`}>{meta.label}</span>
      </div>

      <p className="desc">{row.item_description}</p>
      <p className="tiny">Serial: {row.serial_or_identifier || 'none'} · Buyer {shortAddr(row.buyer)} · Seller {shortAddr(row.seller)}</p>
      <p className="tiny">Proof deadline: {formatUnix(row.proof_deadline)}</p>

      <div className="row">
        <button className="btn btn-ghost" type="button" onClick={() => shareText(row)}>
          <Clipboard size={14} /> Share with seller
        </button>
      </div>

      {row.proof_urls.length > 0 && (
        <div className="links">
          <span className="tiny">Seller photos and video</span>
          {row.proof_urls.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">{url}</a>)}
        </div>
      )}
      {row.reference_urls.length > 0 && (
        <div className="links">
          <span className="tiny">Independent authentication sources</span>
          {row.reference_urls.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">{url}</a>)}
        </div>
      )}

      {(row.verdict || row.verdict_reason) && (
        <div className={`verdict ${verdictClass}`}>
          {row.verdict === 'FAKE' ? <XCircle size={18} /> : <Sparkles size={18} />}
          <div>
            <strong>{row.verdict || 'No label yet'} · confidence {row.confidence}/100</strong>
            <p>{row.verdict_reason}</p>
            <p className="tiny">{row.settled ? 'Paid out.' : 'Not paid out yet.'}</p>
          </div>
        </div>
      )}

      {canSubmit && (
        <div className="stack">
          <p className="hint">
            {row.status === 'DISPUTED'
              ? 'The last read did not pay anyone. Paste public text pages GenLayer can open (not a login wall), on two different domains.'
              : `Suggested sources for ${categoryLabel(row.item_category)}: ${sources.join(' · ')}. Submit at least 2 links on different domains.`}
          </p>
          <UrlList
            label="Close-up photo or video links"
            urls={proofUrls}
            setUrls={setProofUrls}
            pasteInto={pasteInto}
            updateList={updateList}
            addLabel="Add proof link"
          />
          <UrlList
            label="Independent authentication sources"
            urls={refUrls}
            setUrls={setRefUrls}
            pasteInto={pasteInto}
            updateList={updateList}
            addLabel="Add source"
          />
          <button className="btn btn-primary" type="button" disabled={!configured || pending} onClick={() => onSubmitProof(row)}>
            Submit proof
          </button>
        </div>
      )}

      {isSeller && row.status === 'PENDING_PROOF' && deadlinePassed && (
        <p className="hint">The proof deadline has passed. The buyer can reclaim the full GEN amount.</p>
      )}

      {row.status === 'SUBMITTED' && (
        <button className="btn btn-seal" type="button" disabled={!configured || pending || (!canResolve && Boolean(account) && !isBuyer && !isSeller)} onClick={() => onResolve(row)}>
          {pending ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
          Request AI verification
        </button>
      )}

      {row.status === 'DISPUTED' && (
        <p className="hint">Confidence is below 60. The funds stay in escrow. The seller can submit clearer photos and independent sources, then request verification again.</p>
      )}

      {canRefund && (
        <button className="btn btn-primary" type="button" disabled={!configured || pending} onClick={() => onRefund(row)}>
          Reclaim funds because the seller never submitted proof
        </button>
      )}

      {canRetry && (
        <button className="btn btn-primary" type="button" disabled={!configured || pending} onClick={() => onRetry(row)}>
          <RefreshCw size={16} /> Retry payout
        </button>
      )}

      {!account && <p className="tiny">Connect the buyer or seller wallet to submit proof, request AI verification, claim a refund, or retry a payout.</p>}
    </article>
  );
}
