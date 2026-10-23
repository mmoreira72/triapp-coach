import WidgetKit
import SwiftUI

/// Home-screen and lock-screen widgets: today's form, the coach's plan for today and tomorrow, last night's recovery and the
/// morning brief. The app writes the data into the shared App Group (WidgetBridgePlugin) each time the athlete's page opens;
/// the widget only reads it.
let appGroup = "group.ai.laloop.trisquad"

struct WidgetData: Codable {
    struct Last: Codable {
        let date: String
        let sport: String
        let title: String
        let type: String?
        let tss: Double?
    }
    struct Plan: Codable {
        let sport: String
        let title: String
        let minutes: Int?
        let desc: String?
    }
    struct Recovery: Codable {
        let sleep_h: Double?
        let hrv: Double?
        let hrv_trend: Int?
        let rhr: Double?
        let rhr_trend: Int?
    }
    let athlete: String
    let ctl: Double
    let atl: Double
    let tsb: Double
    let updated: String
    let last: Last?
    let today: Plan?
    let tomorrow: Plan?
    let recovery: Recovery?
    let brief: String?
    struct Home: Codable {
        let lat: Double
        let lon: Double
        let place: String?
    }
    let home: Home?

    static func load() -> WidgetData? {
        guard let s = UserDefaults(suiteName: appGroup)?.string(forKey: "widget"), let d = s.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(WidgetData.self, from: d)
    }

    static let sample = WidgetData(
        athlete: "Atleta", ctl: 45, atl: 50, tsb: -5, updated: "", last: nil,
        today: Plan(sport: "bike", title: "FTP Booster", minutes: 78, desc: "3x10' a 95% do FTP"),
        tomorrow: Plan(sport: "run", title: "Corrida Z2", minutes: 60, desc: nil),
        recovery: Recovery(sleep_h: 7.6, hrv: 34, hrv_trend: 1, rhr: 57, rhr_trend: -1), brief: nil,
        home: Home(lat: 41.15, lon: -8.61, place: "Porto"))
}

struct Entry: TimelineEntry {
    let date: Date
    let data: WidgetData?
}

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> Entry { Entry(date: Date(), data: .sample) }
    func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) {
        completion(Entry(date: Date(), data: WidgetData.load() ?? .sample))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
        // one entry per hour for the next day, so the date turns at midnight even when the app is not opened; the app reloads
        // the widgets when it writes new data
        let d = WidgetData.load(), cal = Calendar.current, now = Date()
        let first = cal.nextDate(after: now, matching: DateComponents(minute: 0), matchingPolicy: .nextTime) ?? now
        let entries = [Entry(date: now, data: d)] + (0..<24).map { Entry(date: first.addingTimeInterval(Double($0) * 3600), data: d) }
        completion(Timeline(entries: entries, policy: .atEnd))
    }
}

/// Same thresholds as the dashboard's form pill (TSB)
func formLabel(_ tsb: Double) -> (String, Color) {
    if tsb > 10 { return ("Fresco", .green) }
    if tsb > -10 { return ("Equilibrado", .secondary) }
    if tsb > -30 { return ("Cansado", .orange) }
    return ("Muito cansado", .red)
}

let sportName = ["run": "Corrida", "bike": "Bike", "swim": "Natação", "strength": "Força"]

/// Sunset for a day and place (NOAA solar position, accurate to about a minute; nil in polar day or night)
func sunset(on day: Date, lat: Double, lon: Double) -> Date? {
    let cal = Calendar(identifier: .gregorian)
    var utc = cal
    utc.timeZone = TimeZone(identifier: "UTC")!
    let comps = cal.dateComponents([.year, .month, .day], from: day)
    guard let noonUTC = utc.date(from: DateComponents(year: comps.year, month: comps.month, day: comps.day, hour: 12)) else { return nil }
    let jd = noonUTC.timeIntervalSince1970 / 86400 + 2440587.5
    let n = jd - 2451545.0 + 0.0008
    let jStar = n - lon / 360
    let m = (357.5291 + 0.98560028 * jStar).truncatingRemainder(dividingBy: 360) * .pi / 180
    let c = 1.9148 * sin(m) + 0.02 * sin(2 * m) + 0.0003 * sin(3 * m)
    let lambda = ((m * 180 / .pi) + c + 180 + 102.9372).truncatingRemainder(dividingBy: 360) * .pi / 180
    let jTransit = 2451545.0 + jStar + 0.0053 * sin(m) - 0.0069 * sin(2 * lambda)
    let decl = asin(sin(lambda) * sin(23.4397 * .pi / 180))
    let phi = lat * .pi / 180
    let cosW = (sin(-0.833 * .pi / 180) - sin(phi) * sin(decl)) / (cos(phi) * cos(decl))
    guard cosW >= -1, cosW <= 1 else { return nil }
    let jSet = jTransit + acos(cosW) * 180 / .pi / 360
    return Date(timeIntervalSince1970: (jSet - 2440587.5) * 86400)
}

/// "TER 14 OUT" and the sunset time, the two things the calendar widget used to give
struct DateHeader: View {
    let day: Date
    let home: WidgetData.Home?
    var body: some View {
        let f = DateFormatter()
        f.locale = Locale(identifier: "pt_PT")
        f.dateFormat = "EEE d MMM"
        let t = DateFormatter()
        t.locale = Locale(identifier: "pt_PT")
        t.dateFormat = "HH:mm"
        let set = home.flatMap { sunset(on: day, lat: $0.lat, lon: $0.lon) }
        return HStack(spacing: 6) {
            Text(f.string(from: day).replacingOccurrences(of: ".", with: "").uppercased())
                .font(.caption).fontWeight(.bold).foregroundStyle(.red)
            Spacer(minLength: 4)
            if let s = set {
                Label(t.string(from: s), systemImage: "sunset.fill").font(.caption2).foregroundStyle(.secondary).labelStyle(.titleAndIcon)
            }
        }
    }
}

func planLine(_ p: WidgetData.Plan) -> String {
    "\(sportName[p.sport] ?? p.sport.capitalized)\(p.minutes.map { " · \($0) min" } ?? "")"
}

/// Arrow against the athlete's average, coloured by what is good for that measure
func trend(_ t: Int?, goodWhenUp: Bool) -> Text {
    guard let t = t, t != 0 else { return Text(" =").foregroundColor(.secondary) }
    let good = (t > 0) == goodWhenUp
    return Text(t > 0 ? " ↑" : " ↓").foregroundColor(good ? .green : .orange)
}

struct FormBlock: View {
    let d: WidgetData
    var big = false
    var body: some View {
        let (label, color) = formLabel(d.tsb)
        VStack(alignment: .leading, spacing: 2) {
            Text("FORMA").font(.caption2).foregroundStyle(.secondary)
            // the number on the left; what it means and the two numbers behind it stacked on its right
            HStack(alignment: .center, spacing: 8) {
                Text("\(Int(d.ctl.rounded()))").font(.system(size: big ? 40 : 32, weight: .semibold, design: .rounded))
                VStack(alignment: .leading, spacing: 0) {
                    Text(label).font(.caption).fontWeight(.semibold).foregroundStyle(color)
                    Text("fadiga \(Int(d.atl.rounded()))").font(.caption2).foregroundStyle(.secondary)
                    Text("frescura \(d.tsb >= 0 ? "+" : "")\(Int(d.tsb.rounded()))").font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct RecoveryBlock: View {
    let r: WidgetData.Recovery
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text("NOITE").font(.caption2).foregroundStyle(.secondary)
            if let s = r.sleep_h { Text("Sono \(String(format: "%.1f", s).replacingOccurrences(of: ".", with: ",")) h").font(.caption2) }
            if let h = r.hrv { (Text("HRV \(Int(h.rounded()))") + trend(r.hrv_trend, goodWhenUp: true)).font(.caption2) }
            if let f = r.rhr { (Text("FC rep. \(Int(f.rounded()))") + trend(r.rhr_trend, goodWhenUp: false)).font(.caption2) }
        }
    }
}

struct PlanBlock: View {
    let title: String
    let p: WidgetData.Plan
    var descLines = 2
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text("\(title) · \(planLine(p))").font(.caption2).foregroundStyle(.secondary)
            Text(p.title).font(.footnote).fontWeight(.semibold).lineLimit(2)
            if descLines > 0, let desc = p.desc, !desc.isEmpty {
                Text(desc).font(.caption2).foregroundStyle(.secondary).lineLimit(descLines)
            }
        }
    }
}

struct TriWidgetView: View {
    @Environment(\.widgetFamily) var family
    let entry: Entry

    var body: some View {
        if let d = entry.data {
            switch family {
            case .accessoryInline:
                Text(d.today.map { "Hoje: \($0.title)" } ?? "Forma \(Int(d.ctl.rounded())) · \(formLabel(d.tsb).0)")
            case .accessoryCircular:
                VStack(spacing: 0) {
                    Text("\(Int(d.ctl.rounded()))").font(.system(size: 20, weight: .semibold, design: .rounded))
                    Text(formLabel(d.tsb).0).font(.system(size: 9)).lineLimit(1).minimumScaleFactor(0.6)
                }
            case .accessoryRectangular:
                if let t = d.today {
                    VStack(alignment: .leading, spacing: 0) {
                        Text("HOJE · \(planLine(t))").font(.caption2)
                        Text(t.title).font(.headline).lineLimit(2)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 0) {
                        Text("FORMA \(Int(d.ctl.rounded()))").font(.headline)
                        Text("\(formLabel(d.tsb).0) · nada planeado hoje").font(.caption2)
                    }
                }
            case .systemMedium:
                VStack(alignment: .leading, spacing: 4) {
                DateHeader(day: entry.date, home: d.home)
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 6) {
                        FormBlock(d: d)
                        if let r = d.recovery { RecoveryBlock(r: r) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    VStack(alignment: .leading, spacing: 6) {
                        if let t = d.today {
                            PlanBlock(title: "HOJE", p: t, descLines: d.tomorrow == nil ? 4 : 2)
                        } else {
                            Text("Nada planeado hoje").font(.caption).foregroundStyle(.secondary)
                        }
                        if let m = d.tomorrow { PlanBlock(title: "AMANHÃ", p: m, descLines: 0) }
                        Spacer(minLength: 0)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                }
            case .systemLarge:
                VStack(alignment: .leading, spacing: 8) {
                    DateHeader(day: entry.date, home: d.home)
                    HStack(alignment: .top) {
                        FormBlock(d: d, big: true)
                        Spacer()
                        if let r = d.recovery { RecoveryBlock(r: r) }
                    }
                    Divider()
                    if let t = d.today {
                        PlanBlock(title: "HOJE", p: t, descLines: 5)
                    } else {
                        Text("Nada planeado hoje").font(.footnote).foregroundStyle(.secondary)
                    }
                    if let b = d.brief, !b.isEmpty {
                        Text(b).font(.caption).lineLimit(5)
                    }
                    if let m = d.tomorrow { PlanBlock(title: "AMANHÃ", p: m, descLines: 0) }
                    Spacer(minLength: 0)
                    if let l = d.last {
                        Text("Último: \(sportName[l.sport] ?? l.sport) · \(l.title)\(l.tss.map { " · \(Int($0.rounded())) TSS" } ?? "")")
                            .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
            default:  // systemSmall
                VStack(alignment: .leading, spacing: 4) {
                    DateHeader(day: entry.date, home: d.home)
                    FormBlock(d: d)
                    if let t = d.today {
                        Divider().padding(.vertical, 2)
                        Text("HOJE · \(planLine(t))").font(.caption2).foregroundStyle(.secondary)
                        Text(t.title).font(.footnote).fontWeight(.medium).lineLimit(2)
                    }
                    Spacer(minLength: 0)
                }
            }
        } else {
            Text("Abre o Tri Squad para carregar os teus dados.").font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct TriWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "TriWidget", provider: Provider()) { entry in
            TriWidgetView(entry: entry)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Tri Squad")
        .description("A tua forma, o treino de hoje e de amanhã e a recuperação da noite.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge, .accessoryRectangular, .accessoryCircular, .accessoryInline])
    }
}

@main
struct TriWidgetBundle: WidgetBundle {
    var body: some Widget { TriWidget() }
}
