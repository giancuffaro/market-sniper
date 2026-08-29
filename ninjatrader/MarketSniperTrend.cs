#region Using declarations
using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Windows.Media;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

//  MARKET SNIPER — TREND INDICATOR for NinjaTrader 8
//
//  A port of trend.py. Same three signals, same thresholds, same combining
//  rule, so the two apps cannot quietly disagree:
//
//    SLOPE      21-EMA rising or falling, measured in units of the average bar
//               RANGE per bar, with price required on the matching side of it.
//               Normalising by range is what lets one threshold work on MNQ,
//               on MES and on any timeframe - a fixed price or percent band
//               means something different on every instrument.
//    STRUCTURE  higher highs AND higher lows over the last 20 bars, from swing
//               pivots. Both halves required: higher highs with lower lows is
//               a widening range, not an uptrend.
//    VOLUME     is volume arriving on up-bars or down-bars, with each bar
//               capped at 12x the window median first so one bad print cannot
//               decide the vote.
//
//  Output is up / down / chop. Two of three AND nothing voting the other way,
//  or it is chop - a 2-1 split is a market arguing with itself.
//
//  IT ALSO CLOSES THE ONE-WAY GAP.
//  The Sniper's NinjaTrader link is fire-and-forget: it drops oif_*.txt files
//  into the incoming folder and NinjaTrader executes them. Nothing comes back.
//  With ExportState on, this indicator writes its reading to a small text file
//  that the Sniper can read, which makes the link two-way for data without
//  touching the order path.
//
//  KEEPING THE TWO IN STEP: if you change a threshold here, change it in
//  trend.py as well. They are duplicated on purpose - NinjaScript cannot call
//  Python - and duplicated constants drift unless someone is deliberate.

namespace NinjaTrader.NinjaScript.Indicators
{
    public class MarketSniperTrend : Indicator
    {
        // ---- must match trend.py exactly ----------------------------------
        private const int    EMA_PERIOD     = 21;
        private const int    SLOPE_BARS     = 5;
        private const double SLOPE_MIN      = 0.05;   // bar-ranges per bar
        private const int    STRUCTURE_BARS = 20;
        private const int    SWING          = 3;
        private const int    VOLUME_BARS    = 20;
        private const double VOL_CONFIRM    = 0.55;
        private const double OUTLIER_X      = 12.0;
        private const int    RANGE_BARS     = 20;

        private EMA   ema;
        private int   slopeVote, structVote, volVote, totalScore;
        private string state = "chop";
        private double lastSlope, lastUpShare;
        private DateTime lastExport = DateTime.MinValue;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description                  = "Market Sniper trend: EMA slope, swing structure and volume, combined.";
                Name                         = "MarketSniperTrend";
                Calculate                    = Calculate.OnBarClose;
                IsOverlay                    = false;
                DisplayInDataBox             = true;
                DrawOnPricePanel             = false;
                PaintPriceMarkers            = true;
                IsSuspendedWhileInactive     = true;

                ExportState                  = false;
                ExportPath                   = @"C:\Users\Hulk\Desktop\Market Sniper\logs\nt_trend.txt";
                ExportSeconds                = 15;

                AddPlot(Brushes.Gray, "Trend");     // +1 up, -1 down, 0 chop
            }
            else if (State == State.DataLoaded)
            {
                ema = EMA(Close, EMA_PERIOD);
            }
        }

        protected override void OnBarUpdate()
        {
            // Enough history for the longest window any signal needs.
            int need = Math.Max(EMA_PERIOD + SLOPE_BARS + 1,
                       Math.Max(STRUCTURE_BARS + SWING + 1, VOLUME_BARS)) + 1;
            if (CurrentBar < need)
            {
                Value[0] = 0;
                return;
            }

            slopeVote  = SlopeVote();
            structVote = StructureVote();
            volVote    = VolumeVote();

            totalScore = slopeVote + structVote + volVote;

            // Two of three AND nothing pulling the other way. Identical to
            // direction() in trend.py.
            bool anyUp   = slopeVote > 0 || structVote > 0 || volVote > 0;
            bool anyDown = slopeVote < 0 || structVote < 0 || volVote < 0;

            if (totalScore >= 2 && !anyDown)      state = "up";
            else if (totalScore <= -2 && !anyUp)  state = "down";
            else                                  state = "chop";

            Value[0] = state == "up" ? 1 : (state == "down" ? -1 : 0);
            PlotBrushes[0][0] = state == "up" ? Brushes.LimeGreen
                              : state == "down" ? Brushes.OrangeRed
                              : Brushes.Gray;

            if (ExportState)
                WriteState();
        }

        // ---- SLOPE ---------------------------------------------------------
        // The old multi-timeframe panel compared EMA LEVELS, so a market that
        // had already rolled over still read UP while the fast line sat above
        // the slow one. Slope plus price position is the fix.
        private int SlopeVote()
        {
            double avgRange = AverageRange(RANGE_BARS);
            if (avgRange <= 0)
                return 0;

            double perBar = (ema[0] - ema[SLOPE_BARS]) / SLOPE_BARS;
            lastSlope = perBar / avgRange;
            bool above = Close[0] > ema[0];

            if (lastSlope >= SLOPE_MIN && above)   return 1;
            if (lastSlope <= -SLOPE_MIN && !above) return -1;
            return 0;
        }

        private double AverageRange(int n)
        {
            double sum = 0;
            int used = 0;
            for (int i = 0; i < n && i <= CurrentBar; i++)
            {
                sum += High[i] - Low[i];
                used++;
            }
            return used > 0 ? sum / used : 0;
        }

        // ---- STRUCTURE ------------------------------------------------------
        // Swing pivots: a bar higher (or lower) than SWING bars on BOTH sides.
        // Requiring both sides is what stops the most recent bar registering as
        // a pivot before the market has confirmed it.
        private int StructureVote()
        {
            var highs = new List<double>();
            var lows  = new List<double>();

            for (int i = SWING; i < STRUCTURE_BARS - SWING; i++)
            {
                bool isHigh = true, isLow = true;
                for (int j = i - SWING; j <= i + SWING; j++)
                {
                    if (High[j] > High[i]) isHigh = false;
                    if (Low[j]  < Low[i])  isLow  = false;
                }
                // Bars are indexed backwards in NinjaScript (0 is newest), so
                // walking i upward walks BACK in time. Insert at 0 to end up
                // with oldest-first, matching the Python.
                if (isHigh) highs.Insert(0, High[i]);
                if (isLow)  lows.Insert(0, Low[i]);
            }

            if (highs.Count < 2 || lows.Count < 2)
                return 0;

            double h1 = highs[highs.Count - 1], h0 = highs[highs.Count - 2];
            double l1 = lows[lows.Count - 1],  l0 = lows[lows.Count - 2];

            bool hh = h1 > h0, hl = l1 > l0;
            bool lh = h1 < h0, ll = l1 < l0;

            if (hh && hl) return 1;
            if (lh && ll) return -1;
            return 0;      // widening or narrowing range - not a trend
        }

        // ---- VOLUME ---------------------------------------------------------
        private int VolumeVote()
        {
            var vols = new List<double>();
            for (int i = 0; i < VOLUME_BARS && i <= CurrentBar; i++)
                if (Volume[i] > 0)
                    vols.Add(Volume[i]);

            if (vols.Count < 5)
                return 0;

            var sorted = new List<double>(vols);
            sorted.Sort();
            double median  = sorted[sorted.Count / 2];
            double ceiling = median * OUTLIER_X;

            double up = 0, down = 0;
            for (int i = 0; i < VOLUME_BARS && i <= CurrentBar; i++)
            {
                double v = Math.Min(Volume[i], ceiling);
                if (Close[i] > Open[i])      up   += v;
                else if (Close[i] < Open[i]) down += v;
            }

            double total = up + down;
            if (total <= 0)
                return 0;

            lastUpShare = up / total;
            if (lastUpShare >= VOL_CONFIRM)       return 1;
            if ((1 - lastUpShare) >= VOL_CONFIRM) return -1;
            return 0;
        }

        // ---- EXPORT ---------------------------------------------------------
        // Deliberately best-effort. A locked or missing file must never throw
        // out of OnBarUpdate and take the indicator - or the chart - down with
        // it. Written to a temp name and moved, so the Sniper can never read a
        // half-written line.
        private void WriteState()
        {
            if ((DateTime.Now - lastExport).TotalSeconds < ExportSeconds)
                return;
            lastExport = DateTime.Now;

            try
            {
                string dir = Path.GetDirectoryName(ExportPath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    Directory.CreateDirectory(dir);

                var sb = new StringBuilder();
                sb.AppendLine("instrument=" + Instrument.MasterInstrument.Name);
                sb.AppendLine("timeframe=" + BarsPeriod.Value + " " + BarsPeriod.BarsPeriodType);
                sb.AppendLine("state=" + state);
                sb.AppendLine("score=" + totalScore.ToString());
                sb.AppendLine("slope_vote=" + slopeVote.ToString());
                sb.AppendLine("slope=" + lastSlope.ToString("F3"));
                sb.AppendLine("structure_vote=" + structVote.ToString());
                sb.AppendLine("volume_vote=" + volVote.ToString());
                sb.AppendLine("up_share=" + (lastUpShare * 100).ToString("F1"));
                sb.AppendLine("price=" + Close[0].ToString("F2"));
                sb.AppendLine("bar_time=" + Time[0].ToString("yyyy-MM-dd HH:mm:ss"));
                sb.AppendLine("written=" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));

                string tmp = ExportPath + ".tmp";
                File.WriteAllText(tmp, sb.ToString());
                if (File.Exists(ExportPath))
                    File.Delete(ExportPath);
                File.Move(tmp, ExportPath);
            }
            catch (Exception)
            {
                // Swallowed on purpose: a charting indicator must not be able
                // to fail because a text file was locked.
            }
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Export state to file", Order = 1, GroupName = "Market Sniper",
                 Description = "Write the reading to a file the Sniper can read. The order link is one-way; this is how data comes back.")]
        public bool ExportState { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Export path", Order = 2, GroupName = "Market Sniper")]
        public string ExportPath { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Export every (seconds)", Order = 3, GroupName = "Market Sniper")]
        public int ExportSeconds { get; set; }

        [Browsable(false)]
        [XmlIgnore]
        public Series<double> Trend { get { return Values[0]; } }

        [Browsable(false)]
        [XmlIgnore]
        public string TrendState { get { return state; } }
        #endregion
    }
}
