import Foundation
import Capacitor
import HealthKit

/// Reads daily recovery data from Apple Health (sleep, resting heart rate, HRV) for the page to send to the box.
/// JS: Capacitor.Plugins.Health.isAvailable() / requestAuthorization() / daily({days})
@objc(HealthPlugin)
public class HealthPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "HealthPlugin"
    public let jsName = "Health"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "isAvailable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "requestAuthorization", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "daily", returnType: CAPPluginReturnPromise)
    ]

    private let store = HKHealthStore()
    private var readTypes: Set<HKObjectType> {
        [
            HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!,
            HKObjectType.quantityType(forIdentifier: .restingHeartRate)!,
            HKObjectType.quantityType(forIdentifier: .heartRateVariabilitySDNN)!
        ]
    }

    @objc func isAvailable(_ call: CAPPluginCall) {
        call.resolve(["available": HKHealthStore.isHealthDataAvailable()])
    }

    @objc func requestAuthorization(_ call: CAPPluginCall) {
        guard HKHealthStore.isHealthDataAvailable() else {
            call.resolve(["granted": false])
            return
        }
        // HealthKit never says whether reading was allowed (privacy): "granted" only means the sheet was answered
        store.requestAuthorization(toShare: nil, read: readTypes) { ok, _ in
            call.resolve(["granted": ok])
        }
    }

    /// One row per day for the last `days` days: sleep hours (night ending that day), resting HR (bpm), HRV SDNN (ms).
    @objc func daily(_ call: CAPPluginCall) {
        let days = max(1, min(call.getInt("days") ?? 30, 120))
        let cal = Calendar.current
        let end = cal.startOfDay(for: Date()).addingTimeInterval(86400)
        let start = cal.date(byAdding: .day, value: -days, to: end)!
        var rows: [String: [String: Any]] = [:]
        let group = DispatchGroup()
        let lock = NSLock()
        func put(_ day: Date, _ key: String, _ value: Double) {
            lock.lock()
            defer { lock.unlock() }
            let d = HealthPlugin.dayString(day)
            var r = rows[d] ?? ["date": d]
            r[key] = (value * 10).rounded() / 10
            rows[d] = r
        }

        for (id, unit, key) in [
            (HKQuantityTypeIdentifier.restingHeartRate, HKUnit.count().unitDivided(by: .minute()), "rhr"),
            (HKQuantityTypeIdentifier.heartRateVariabilitySDNN, HKUnit.secondUnit(with: .milli), "hrv")
        ] {
            group.enter()
            let q = HKStatisticsCollectionQuery(
                quantityType: HKObjectType.quantityType(forIdentifier: id)!,
                quantitySamplePredicate: HKQuery.predicateForSamples(withStart: start, end: end),
                options: .discreteAverage,
                anchorDate: start,
                intervalComponents: DateComponents(day: 1)
            )
            q.initialResultsHandler = { _, result, _ in
                result?.enumerateStatistics(from: start, to: end) { s, _ in
                    if let v = s.averageQuantity()?.doubleValue(for: unit) { put(s.startDate, key, v) }
                }
                group.leave()
            }
            store.execute(q)
        }

        // Sleep: merge overlapping "asleep" intervals (watch and phone both record) and credit them to the wake-up day
        group.enter()
        let sleepStart = start.addingTimeInterval(-12 * 3600)
        let sq = HKSampleQuery(
            sampleType: HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!,
            predicate: HKQuery.predicateForSamples(withStart: sleepStart, end: end),
            limit: HKObjectQueryNoLimit,
            sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)]
        ) { _, samples, _ in
            let asleep: Set<Int> = [1, 3, 4, 5]  // unspecified, core, deep, REM (not in bed = 0, awake = 2)
            var merged: [(Date, Date)] = []
            for s in (samples as? [HKCategorySample] ?? []) where asleep.contains(s.value) {
                if let last = merged.last, s.startDate <= last.1 {
                    merged[merged.count - 1].1 = max(last.1, s.endDate)
                } else {
                    merged.append((s.startDate, s.endDate))
                }
            }
            var byDay: [Date: Double] = [:]
            for (a, b) in merged {
                // a nap or night is credited to the day it ends (after 6 pm it counts for the next morning)
                let day = cal.startOfDay(for: b.addingTimeInterval(6 * 3600))
                byDay[day, default: 0] += b.timeIntervalSince(a) / 3600
            }
            for (day, h) in byDay where day >= start && day < end { put(day, "sleep_h", h) }
            group.leave()
        }
        store.execute(sq)

        // raw sample counts per type in the window: tells "no data in Health" apart from a reading problem
        var counts: [String: Int] = [:]
        for (name, type) in [
            ("sleep", HKObjectType.categoryType(forIdentifier: .sleepAnalysis)! as HKSampleType),
            ("rhr", HKObjectType.quantityType(forIdentifier: .restingHeartRate)! as HKSampleType),
            ("hrv", HKObjectType.quantityType(forIdentifier: .heartRateVariabilitySDNN)! as HKSampleType)
        ] {
            group.enter()
            store.execute(HKSampleQuery(sampleType: type, predicate: HKQuery.predicateForSamples(withStart: sleepStart, end: end),
                                        limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, samples, error in
                lock.lock()
                counts[name] = error == nil ? (samples?.count ?? 0) : -1
                lock.unlock()
                group.leave()
            })
        }

        group.notify(queue: .main) {
            call.resolve(["days": rows.values.sorted { ($0["date"] as! String) < ($1["date"] as! String) }, "counts": counts])
        }
    }

    static func dayString(_ d: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: d)
    }
}
