import { parseGenToWei, formatWeiToGen } from '../money.js';

function runUnitConversionTests() {
  console.log('Starting Precision Unit Conversion Tests...');

  const smallestWei = parseGenToWei('0.000000000000000001');
  if (smallestWei !== 1n) throw new Error(`Test 1 Failed: expected 1n, got ${smallestWei}`);
  if (formatWeiToGen(1n) !== '0.000000000000000001') throw new Error(`Test 1 Format Failed: got ${formatWeiToGen(1n)}`);

  const pointOneWei = parseGenToWei('0.1');
  if (pointOneWei !== 100000000000000000n) throw new Error(`Test 2 Failed: got ${pointOneWei}`);
  if (formatWeiToGen(100000000000000000n) !== '0.1') throw new Error(`Test 2 Format Failed: got ${formatWeiToGen(100000000000000000n)}`);

  const millionWei = parseGenToWei('1000000');
  if (millionWei !== 1000000000000000000000000n) throw new Error(`Test 3 Failed: got ${millionWei}`);
  if (formatWeiToGen(1000000000000000000000000n) !== '1000000') throw new Error('Test 3 Format Failed');

  const testValues = ['1', '0.5', '100.25', '0.000001', '30000', '123.456'];
  for (const val of testValues) {
    const formatted = formatWeiToGen(parseGenToWei(val));
    if (formatted !== val) throw new Error(`Round-trip Failed for ${val}: got ${formatted}`);
  }

  const stewardWei = parseGenToWei('123.456');
  if (stewardWei !== 123456000000000000000n) throw new Error(`Test 5 Failed: got ${stewardWei}`);
  if (formatWeiToGen(stewardWei) !== '123.456') throw new Error('Test 5 Format Failed');

  if (parseGenToWei('') !== 0n) throw new Error('Empty input must be 0');
  if (parseGenToWei('abc') !== 0n) throw new Error('Invalid input must be 0');
  if (parseGenToWei('0') !== 0n) throw new Error('Zero must be 0');

  console.log('All Precision Unit Conversion Tests Passed 100%!');
}

runUnitConversionTests();
