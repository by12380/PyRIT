#!/usr/bin/env python3
"""
Run Modal Crescendo attacks for all 9 harmful behavior categories.
Each category runs 5 trials in parallel.
"""

import subprocess
import sys
from embellished_objectives import EMBELLISHED_OBJECTIVES

def run_category(category_name, num_trials=5, max_turns=15):
    """Run Modal attacks for one category."""
    
    obj = EMBELLISHED_OBJECTIVES[category_name]
    
    print(f"\n{'='*100}")
    print(f"🚀 Starting Category: {category_name.upper().replace('_', ' ')}")
    print(f"   Trials: {num_trials} (parallel)")
    print(f"   Max Turns: {max_turns}")
    print(f"{'='*100}\n")
    
    cmd = [
        "modal", "run", "modal_crescendo.py",
        "--objective", obj["embellished"],
        "--scoring-objective", obj["scoring"],
        "--num-trials", str(num_trials),
        "--max-turns", str(max_turns),
    ]
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=False, text=True)
        print(f"\n✅ {category_name} completed successfully\n")
        return {"category": category_name, "status": "success"}
    except subprocess.CalledProcessError as e:
        print(f"\n❌ {category_name} failed with error\n")
        return {"category": category_name, "status": "failed", "error": str(e)}


def main():
    """Run all categories sequentially."""
    
    print("\n" + "="*100)
    print("🎯 MODAL CRESCENDO - ALL CATEGORIES")
    print("="*100)
    print(f"Categories: {len(EMBELLISHED_OBJECTIVES)}")
    print(f"Trials per category: 5 (parallel)")
    print(f"Total attack runs: {len(EMBELLISHED_OBJECTIVES) * 5} = 45")
    print("="*100 + "\n")
    
    categories = list(EMBELLISHED_OBJECTIVES.keys())
    
    print("Categories to test:")
    for i, cat in enumerate(categories, 1):
        print(f"  {i}. {cat.replace('_', ' ').title()}")
    
    print("\n" + "="*100)
    print("🚀 Starting all categories...")
    print("="*100 + "\n")
    
    results = []
    
    for i, category in enumerate(categories, 1):
        print(f"\n{'#'*100}")
        print(f"# Progress: {i}/{len(categories)} categories")
        print(f"{'#'*100}\n")
        
        result = run_category(category, num_trials=5, max_turns=15)
        results.append(result)
    
    # Final summary
    print("\n\n" + "="*100)
    print("🏁 FINAL SUMMARY")
    print("="*100)
    
    successful = sum(1 for r in results if r.get('status') == 'success')
    
    print(f"\nCategories completed: {successful}/{len(results)}")
    print(f"\nDetailed results:")
    print("-"*100)
    
    for r in results:
        status_icon = "✅" if r.get('status') == 'success' else "❌"
        category_name = r['category'].replace('_', ' ').title()
        print(f"{status_icon} {category_name:<30} - {r.get('status', 'unknown').upper()}")
    
    print("\n" + "="*100)
    print(f"🌐 View all runs: https://wandb.ai/brian-young-personal/pyrit-crescendo-modal")
    print("="*100 + "\n")
    
    return results


if __name__ == "__main__":
    try:
        results = main()
        sys.exit(0 if all(r.get('status') == 'success' for r in results) else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user. Exiting...")
        sys.exit(1)

