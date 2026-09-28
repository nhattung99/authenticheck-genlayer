const WEI_PER_GEN = 1000000000000000000n;

export const parseGenToWei = (genAmountStr) => {
  if (!genAmountStr) return 0n;
  const str = String(genAmountStr).trim();
  if (!/^\d+(\.\d+)?$/.test(str)) return 0n;
  const [intPart, fracPartRaw = ""] = str.split(".");
  const fracPart = (fracPartRaw + "0".repeat(18)).slice(0, 18);
  const wei = BigInt(intPart + fracPart);
  return wei > 0n ? wei : 0n;
};

export const formatWeiToGen = (val) => {
  if (val === null || val === undefined || val === '') return '0';
  let wei;
  try { wei = BigInt(val); } catch { return String(val); }
  if (wei === 0n) return '0';
  const intPart = wei / WEI_PER_GEN;
  const fracPart = wei % WEI_PER_GEN;
  if (fracPart === 0n) return intPart.toString();
  const fracStr = fracPart.toString().padStart(18, "0").replace(/0+$/, "");
  return fracStr.length > 0 ? `${intPart}.${fracStr}` : intPart.toString();
};

export const sanitizeGenInput = (raw) => {
  const str = String(raw ?? '');
  let out = '';
  let seenDot = false;
  let fracCount = 0;
  for (const ch of str) {
    if (ch >= '0' && ch <= '9') {
      if (seenDot) {
        if (fracCount >= 18) continue;
        fracCount += 1;
      }
      out += ch;
    } else if (ch === '.' && !seenDot) {
      seenDot = true;
      out += ch;
    }
  }
  return out;
};

export const toUnixString = (val) => {
  if (val === null || val === undefined || val === '') return '0';
  if (typeof val === 'bigint') return val.toString();
  if (typeof val === 'number') {
    if (!Number.isFinite(val) || !Number.isInteger(val) || val < 0) return '0';
    return BigInt(val).toString();
  }
  const str = String(val).trim();
  if (!/^\d+$/.test(str)) return '0';
  return str;
};

export const toWeiString = (val) => {
  if (val === null || val === undefined || val === '') return '0';
  if (typeof val === 'bigint') return val.toString();
  if (typeof val === 'number') return '0';
  const str = String(val).trim();
  if (!/^\d+$/.test(str)) return '0';
  return str;
};

export const toPercentInt = (val) => {
  if (val === null || val === undefined || val === '') return 0;
  try {
    const asInt = typeof val === 'bigint' ? val : BigInt(String(val).split('.')[0] || '0');
    if (asInt < 0n) return 0;
    if (asInt > 100n) return 100;
    return Number(asInt);
  } catch {
    return 0;
  }
};

export const unixNowSeconds = () => BigInt(Date.now()) / 1000n;

export const deadlineFromOffset = (secondsStr) => {
  const secs = BigInt(String(secondsStr));
  return unixNowSeconds() + secs;
};

export const formatUnix = (val) => {
  try {
    const secs = BigInt(String(val));
    const ms = Number(secs * 1000n);
    if (!Number.isFinite(ms)) return String(val);
    return new Date(ms).toLocaleString('en-US');
  } catch {
    return String(val ?? '');
  }
};
