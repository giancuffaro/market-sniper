#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Xml.Serialization;
using System.IO;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.SuperDom;
using NinjaTrader.Gui.Tools;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.Core.FloatingPoint;
using NinjaTrader.NinjaScript.DrawingTools;
using NinjaTrader.NinjaScript.Strategies;
#endregion

//  MARKET SNIPER — RATCHET STOP for NinjaTrader 8
//
//  A port of the ratchet in futures_client.py, running INSIDE NinjaTrader so it
//  no longer depends on the Sniper being open.
//
//  WHY THIS EXISTS
//  The Sniper's link to NinjaTrader is one-way: it drops order files and hears
//  nothing back. The ratchet therefore ran in the app, on a one-second poll,
//  and only while the app was open. Two consequences, both real:
//    - a limit resting overnight could fill at Sunday's 18:00 ET reopen with
//      nothing managing it until someone opened the app;
//    - closing the laptop mid-trade left the position unprotected.
//  Here the stop is a REAL order at the exchange. It survives the app, the
//  browser, and the machine going to sleep.
//
//  WHAT IT DOES, AND ONLY THIS
//  It NEVER enters a trade. It watches whatever position the account already
//  has - opened by hand, by the Sniper, or by anything else - and keeps a
//  protective stop behind it:
//
//      open          stop = entry - STEP        (long; mirrored for short)
//      +1 step       stop = breakeven
//      +2 steps      stop = entry + 1 step
//      +3 steps      stop = entry + 2 steps     ... no cap
//
//  The stop only ever moves in your favour. It is never widened, never pulled
//  back, and never cancelled while the position is open.
//
//  DEFAULT STEP IS 12.5 POINTS ON MNQ - $25 a contract, 50 ticks. Matches
//  ratchet_points in futures_client.py. If you change one, change the other:
//  they are duplicated because NinjaScript cannot call Python.

namespace NinjaTrader.NinjaScript.Strategies
{
    public class MarketSniperRatchet : Strategy
    {
        private double peakPoints;          // best excursion, in points, this position
        private double lastStopPrice;       // where our stop currently sits
        private int    lastQty;
        private MarketPosition lastPos = MarketPosition.Flat;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Ratchet stop only. Never enters — it protects whatever position the account already holds.";
                Name        = "MarketSniperRatchet";

                // OnEachTick: a stop that only re-evaluates on bar close can sit
                // a whole bar behind the move it is meant to be following.
                Calculate                = Calculate.OnEachTick;
                EntriesPerDirection      = 1;
                EntryHandling            = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                ExitOnSessionCloseSeconds    = 30;
                IsFillLimitOnTouch       = false;
                BarsRequiredToTrade      = 1;
                IsUnmanaged              = false;

                // THE IMPORTANT ONE. Without this the strategy ignores a
                // position it did not open itself - which is every position
                // here, since this thing never enters.
                StartBehavior            = StartBehavior.AdoptAccountPosition;
                IsAdoptAccountPositionAware = true;

                StepPoints  = 12.5;
                EnableAlerts = true;
            }
        }

        protected override void OnBarUpdate()
        {
            // Flat: forget everything. Carrying a peak across positions would
            // put the next trade's stop where the last trade's high was.
            if (Position.MarketPosition == MarketPosition.Flat)
            {
                if (lastPos != MarketPosition.Flat)
                {
                    peakPoints    = 0;
                    lastStopPrice = 0;
                    lastQty       = 0;
                    lastPos       = MarketPosition.Flat;
                }
                return;
            }

            // A new position, or a changed size, restarts the ratchet.
            if (Position.MarketPosition != lastPos || Position.Quantity != lastQty)
            {
                peakPoints    = 0;
                lastStopPrice = 0;
                lastPos       = Position.MarketPosition;
                lastQty       = Position.Quantity;
            }

            double entry = Position.AveragePrice;
            double last  = Close[0];
            bool   isLong = Position.MarketPosition == MarketPosition.Long;
            double pts   = isLong ? (last - entry) : (entry - last);

            // High-water mark. Only ever up - that is what makes it a ratchet
            // rather than a stop that can loosen again.
            if (pts > peakPoints)
                peakPoints = pts;

            // Which rung the best excursion has taken, and the stop one step
            // below it. The 1e-9 matters: an exact +12.5 touch computes as
            // 12.499999999999998 often enough to leave the stop a rung low,
            // and landing exactly on a rung is the normal case here.
            double rung     = Math.Floor(peakPoints / StepPoints + 1e-9) * StepPoints;
            double stopPts  = rung - StepPoints;
            double stopRaw  = isLong ? entry + stopPts : entry - stopPts;

            // Snap to the instrument's tick or the broker rejects the order.
            double stopPrice = Instrument.MasterInstrument.RoundToTickSize(stopRaw);

            // Never move it against yourself. Equally important: do not spam
            // the broker with an amend on every tick when nothing changed.
            if (lastStopPrice != 0)
            {
                if (isLong  && stopPrice <= lastStopPrice) return;
                if (!isLong && stopPrice >= lastStopPrice) return;
            }

            // A stop already through the market would be rejected, and the
            // position would be left with NO stop at all - worse than the one
            // we were replacing. Leave the existing one alone and try again on
            // the next tick.
            if (isLong  && stopPrice >= last) return;
            if (!isLong && stopPrice <= last) return;

            SetStopLoss(CalculationMode.Price, stopPrice);
            lastStopPrice = stopPrice;

            if (EnableAlerts)
                Print(String.Format(
                    "{0}  RATCHET  {1} {2} @ {3}  best +{4:F2} pts  ->  stop {5} ({6}{7:F2} pts)",
                    Time[0], Position.MarketPosition, Position.Quantity, entry,
                    peakPoints, stopPrice, stopPts >= 0 ? "+" : "", stopPts));
        }

        #region Properties
        [NinjaScriptProperty]
        [Range(0.25, double.MaxValue)]
        [Display(Name = "Step (points)", Order = 1, GroupName = "Market Sniper",
                 Description = "Stop distance AND rung size. 12.5 on MNQ = $25 a contract, 50 ticks. Must match ratchet_points in futures_client.py.")]
        public double StepPoints { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Print each move", Order = 2, GroupName = "Market Sniper",
                 Description = "Log every stop move to the Output window. Leave on until you trust it.")]
        public bool EnableAlerts { get; set; }
        #endregion
    }
}
