"""
Test script for Advisory generation
"""
import sys
from pathlib import Path
import asyncio

# Ensure backend package can be imported
sys.path.append(str(Path(__file__).resolve().parent.parent / "backend"))

from pipeline.step08_advisory import generate_advisory_for_district

async def main():
    print("Testing Advisory Generation & Fact-Checker\n" + "="*50)
    
    districts = ["south_24_parganas", "kolkata", "east_medinipur"]
    
    for d in districts:
        print(f"\n--- Generating for {d} ---")
        res = await generate_advisory_for_district("amphan", d)
        
        print("Verified:", res["verified"])
        print("Output:", res["content"])

if __name__ == "__main__":
    asyncio.run(main())
