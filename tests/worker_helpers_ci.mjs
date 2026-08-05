const RealDate = Date;
const FIXED_NOW = "2026-08-05T12:00:00.000Z";

class FixedDate extends RealDate {
  constructor(...args) {
    super(...(args.length ? args : [FIXED_NOW]));
  }

  static now() {
    return RealDate.parse(FIXED_NOW);
  }

  static parse(value) {
    return RealDate.parse(value);
  }

  static UTC(...args) {
    return RealDate.UTC(...args);
  }
}

globalThis.Date = FixedDate;

await import("./worker_helpers.test.mjs");
